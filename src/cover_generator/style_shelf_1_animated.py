"""
样式四动图（优化版）：
- 背景：低幅度 Ken Burns 慢推，按槽位 1→5 轮换 5 张背景图
- 底栏五槽位：固定不动
- 整幅均匀黑罩做轻微“呼吸”透明度
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
    _plain_text_top_left,
    _primary_strip_candidates_ordered,
    _shelf_title_block_height,
    _split_equal_int,
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


def _background_cycle_paths(
    slot_tile_paths: list[Path],
    primary_pool: list[tuple[int, Path]],
    fanart_pool: list[tuple[int, Path]],
    fallback_bg: Path,
    max_count: int = 5,
) -> list[Path]:
    """
    与槽位数据一一对应构建背景序列：
    - 第 i 个槽位对应第 i 段背景（i=1..5）
    - 每槽优先 fanart_i，无则回退该槽 primary_i
    - 不做去重，不额外插入“默认背景”
    """
    primary_slot_by_path: dict[Path, int] = {}
    for slot, p in primary_pool:
        primary_slot_by_path.setdefault(p, slot)
    fanart_by_slot: dict[int, Path] = {slot: p for slot, p in fanart_pool}

    paths: list[Path] = []
    for p in slot_tile_paths[:max_count]:
        slot = primary_slot_by_path.get(p)
        bg = fanart_by_slot.get(slot) if slot is not None else None
        chosen = bg or p
        paths.append(chosen)
    while len(paths) < max_count:
        paths.append(fallback_bg)
    return paths[:max_count]


def _first_five_slot_paths(candidates: list[Path]) -> list[Path] | None:
    """严格使用槽位区前 5 个媒体，不做补位。"""
    if len(candidates) < 5:
        return None
    return candidates[:5]


def create_style_shelf_1_animated(
    library_dir,
    title,
    font_path,
    font_size=(0.86, 0.94),
    animation_duration=8,
    animation_fps=24,
    animation_format="gif",
    output_width=400,
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

        bg_path = primary_by_slot.get(1)
        if not bg_path:
            logger.error("style_shelf_1_animated: no slot 1 primary in %s", library_dir)
            return False

        thumbs = _primary_strip_candidates_ordered(primary_by_slot, None)
        out_w = max(120, int(output_width or 400))
        scale = out_w / float(CANVAS_W)
        w, h = out_w, max(68, int(CANVAS_H * scale))
        tile_paths = _first_five_slot_paths(thumbs)
        if not tile_paths:
            logger.error(
                "style_shelf_1_animated: require at least 5 slot media, got %d",
                len(thumbs),
            )
            return False
        bg_cycle = _background_cycle_paths(
            tile_paths,
            primary_pool,
            fanart_pool,
            bg_path,
            max_count=5,
        )
        kb_planes: list[Image.Image] = []
        for bpath in bg_cycle:
            kb_src = _prepare_kb_plane(bpath)
            kb_plane = kb_src.resize(
                (max(w + 4, int(kb_src.width * scale)), max(h + 4, int(kb_src.height * scale))),
                _LANCZOS,
            )
            kb_planes.append(kb_plane)
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

        tiles_rgba: list[Image.Image] = []
        for slot in range(5):
            tile = _make_tile_rgba(
                tile_paths[slot], cell_w, cell_h, radius, fallbacks=fb_unique
            )
            if tile is None:
                logger.error("style_shelf_1_animated: tile build failed at slot %s", slot)
                return False
            tiles_rgba.append(tile)
        zh_size_min = max(10, int(_SHELF_ZH_SIZE_MIN * scale))
        en_size_min = max(8, int(_SHELF_EN_SIZE_MIN * scale))
        zh_size = max(zh_size_min, int(h * _SHELF_ZH_H_RATIO * zh_ratio))
        en_size = max(en_size_min, int(h * _SHELF_EN_H_RATIO * en_ratio))
        try:
            zh_font = ImageFont.truetype(zh_font_path, zh_size)
        except Exception:
            zh_font = ImageFont.load_default()
        try:
            en_font = ImageFont.truetype(en_font_path, en_size)
        except Exception:
            en_font = ImageFont.load_default()

        margin_left = int(w * 0.042)
        zh_en_gap_min = max(8, int(_SHELF_ZH_EN_GAP_MIN * scale))
        zh_en_gap = max(zh_en_gap_min, int(h * _SHELF_ZH_EN_GAP_H_RATIO))
        block_h = _shelf_title_block_height(
            title_zh, title_en or "", zh_font, en_font, zh_en_gap
        )
        margin_top = max(int(h * 0.02), (row_y - block_h) // 2)

        fps = max(1, int(animation_fps))
        duration = max(2, int(animation_duration))
        # 让配置时长真正生效：此前 240 帧上限会把长时长截短（例如 25s@24fps 被截到约 10s）。
        # 保留较高的安全上限以避免异常配置导致内存飙升。
        n_frames = min(2400, max(12, fps * duration))

        frames: list[Image.Image] = []
        for fi in range(n_frames):
            p = fi / float(n_frames)
            seg_pos = (fi * len(kb_planes)) / float(max(1, n_frames))
            seg_idx = min(int(seg_pos), len(kb_planes) - 1)
            seg_p = seg_pos - float(seg_idx)
            # scaled Ken Burns crop at target output size
            kb_plane = kb_planes[seg_idx]
            pw, ph = kb_plane.size
            max_x = max(0, pw - w)
            max_y = max(0, ph - h)
            u = 2.0 * math.pi * seg_p
            amp = 0.35
            px = int(max_x * (0.5 + 0.5 * amp * math.sin(u)))
            py = int(max_y * (0.5 + 0.5 * amp * math.cos(u * 0.73)))
            canvas = kb_plane.crop((px, py, px + w, py + h)).convert("RGBA")
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
