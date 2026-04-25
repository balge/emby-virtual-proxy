"""
样式四动图（优化版）：
- 背景：低幅度 Ken Burns 慢推（不再做高斯模糊）
- 前景：底栏五槽位保持静态
- 动效：整幅均匀黑罩做轻微“呼吸”透明度
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from PIL import Image, ImageFont, ImageOps

from .animated_utils import encode_apng_base64, encode_gif_base64
from .style_shelf_1 import (
    CANVAS_H,
    CANVAS_W,
    _black_vertical_mask,
    _collect_fanart_pool,
    _collect_primary_pool,
    _composite_at,
    _make_tile_rgba,
    _pick_fanart_background,
    _plain_text_top_left,
    _primary_strip_candidates_ordered,
    _shelf_title_block_height,
    _split_equal_int,
    _tile_paths_for_five_deduped,
    _SHELF_EN_H_RATIO,
    _SHELF_EN_SIZE_MIN,
    _SHELF_ZH_EN_GAP_H_RATIO,
    _SHELF_ZH_EN_GAP_MIN,
    _SHELF_ZH_H_RATIO,
    _SHELF_ZH_SIZE_MIN,
)

logger = logging.getLogger(__name__)

try:
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    _LANCZOS = Image.LANCZOS


def _prepare_kb_plane(bg_path: Path, cover_scale: float = 1.06) -> Image.Image:
    """比画布略大的整图（不模糊），供低幅度 Ken Burns 裁切。"""
    im = Image.open(bg_path).convert("RGB")
    tw = max(CANVAS_W + 4, int(CANVAS_W * cover_scale))
    th = max(CANVAS_H + 4, int(CANVAS_H * cover_scale))
    return ImageOps.fit(im, (tw, th), method=_LANCZOS).convert("RGBA")


def _crop_kb_frame(plane: Image.Image, progress: float) -> Image.Image:
    """progress ∈ [0,1)，小幅正弦平移裁切到画布尺寸。"""
    pw, ph = plane.size
    max_x = max(0, pw - CANVAS_W)
    max_y = max(0, ph - CANVAS_H)
    u = 2.0 * math.pi * progress
    amp = 0.35
    px = int(max_x * (0.5 + 0.5 * amp * math.sin(u)))
    py = int(max_y * (0.5 + 0.5 * amp * math.cos(u * 0.73)))
    return plane.crop((px, py, px + CANVAS_W, py + CANVAS_H)).convert("RGBA")

def _breathing_alpha(progress: float, lo: float = 0.28, hi: float = 0.32) -> float:
    t = 0.5 + 0.5 * math.sin(2.0 * math.pi * progress)
    return lo + (hi - lo) * t


def create_style_shelf_1_animated(
    library_dir,
    title,
    font_path,
    font_size=(0.86, 0.94),
    animation_duration=8,
    animation_fps=24,
    animation_format="gif",
    scroll_direction="alternate",
    image_count=None,
    departure_type=None,
):
    del scroll_direction, image_count, departure_type
    try:
        zh_font_path, en_font_path = font_path
        title_zh, title_en = title
        zh_ratio, en_ratio = float(font_size[0] or 1.0), float(font_size[1] or 1.0)

        folder = Path(library_dir)
        fanart_pool = _collect_fanart_pool(folder)
        primary_pool = _collect_primary_pool(folder)
        primary_by_slot = {s: p for s, p in primary_pool}

        if fanart_pool:
            bg_path, bg_slot, _ = _pick_fanart_background(fanart_pool)
            if not bg_path:
                return False
        else:
            bg_path = primary_by_slot.get(1)
            if not bg_path:
                logger.error("style_shelf_1_animated: no slot 1 primary in %s", library_dir)
                return False
            bg_slot = 1

        thumbs = _primary_strip_candidates_ordered(primary_by_slot, bg_slot)
        kb_plane = _prepare_kb_plane(bg_path)

        w, h = CANVAS_W, CANVAS_H
        bottom_margin = max(14, int(h * 0.022))
        g0 = max(10, int(w * 0.0098))
        max_h = int(min(h * 0.48, 520))
        min_h = max(120, int(h * 0.14))
        pw_cap = (w - 6 * g0) // 5
        pw_h_cap = int(max_h * 2 / 3)
        cell_w = min(pw_cap, pw_h_cap)
        cell_h = int(cell_w * 3 / 2)
        if cell_h > max_h:
            cell_h = max_h
            cell_w = int(cell_h * 2 / 3)
        while cell_h >= min_h:
            spare = w - 5 * cell_w
            if spare >= 6 * 6:
                break
            cell_h -= 2
            cell_w = int(cell_h * 2 / 3)
        spare = w - 5 * cell_w
        gaps = _split_equal_int(spare, 6)
        radius = max(10, min(18, cell_w // 18))
        row_y = h - bottom_margin - cell_h

        x_positions: list[int] = []
        acc = gaps[0]
        for i in range(5):
            x_positions.append(acc)
            acc += cell_w + gaps[i + 1]
        fb_unique: list[Path] = []
        for p in (
            bg_path,
            *[pt for _s, pt in primary_pool],
            *[pt for _s, pt in fanart_pool if pt != bg_path],
        ):
            if p not in fb_unique:
                fb_unique.append(p)

        tile_paths = _tile_paths_for_five_deduped(thumbs, bg_path)
        tiles_rgba: list[Image.Image] = []
        for slot in range(5):
            tile = _make_tile_rgba(
                tile_paths[slot], cell_w, cell_h, radius, fallbacks=fb_unique
            )
            if tile is None:
                logger.error("style_shelf_1_animated: tile build failed at slot %s", slot)
                return False
            tiles_rgba.append(tile)

        zh_size = max(_SHELF_ZH_SIZE_MIN, int(h * _SHELF_ZH_H_RATIO * zh_ratio))
        en_size = max(_SHELF_EN_SIZE_MIN, int(h * _SHELF_EN_H_RATIO * en_ratio))
        try:
            zh_font = ImageFont.truetype(zh_font_path, zh_size)
        except Exception:
            zh_font = ImageFont.load_default()
        try:
            en_font = ImageFont.truetype(en_font_path, en_size)
        except Exception:
            en_font = ImageFont.load_default()

        margin_left = int(w * 0.042)
        zh_en_gap = max(_SHELF_ZH_EN_GAP_MIN, int(h * _SHELF_ZH_EN_GAP_H_RATIO))
        block_h = _shelf_title_block_height(
            title_zh, title_en or "", zh_font, en_font, zh_en_gap
        )
        margin_top = max(int(h * 0.02), (row_y - block_h) // 2)

        fps = max(1, int(animation_fps))
        duration = max(2, int(animation_duration))
        n_frames = min(240, max(12, fps * duration))

        frames: list[Image.Image] = []
        for fi in range(n_frames):
            p = fi / float(n_frames)
            canvas = _crop_kb_frame(kb_plane, p)
            canvas = Image.alpha_composite(
                canvas,
                _black_vertical_mask((w, h), max_alpha_ratio=_breathing_alpha(p)),
            )
            canvas = _plain_text_top_left(
                canvas,
                title_zh,
                title_en or "",
                zh_font,
                en_font,
                margin_left=margin_left,
                margin_top=margin_top,
                zh_en_gap=zh_en_gap,
            )
            for i in range(5):
                canvas = _composite_at(canvas, tiles_rgba[i], (x_positions[i], row_y))
            frames.append(canvas)

        fmt = str(animation_format or "gif").lower()
        if fmt == "apng":
            return encode_apng_base64(frames, fps=fps)
        return encode_gif_base64(frames, fps=fps)
    except Exception as e:
        logger.error("style_shelf_1_animated failed: %s", e, exc_info=True)
        return False
