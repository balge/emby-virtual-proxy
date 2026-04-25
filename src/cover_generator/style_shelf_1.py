"""
Shelf-style library cover（样式四）：背景 + 底栏五海报 + 左上标题。

布局与图层与「猜你喜欢」脚本一致：
- 画布 1920×1080；`1.*` 全幅铺满；整宽上 6 段等宽缝 + 5 张等宽竖条（2:3）、圆角、无阴影。
- 自下而上：背景 → **整幅高斯虚化**（全画布同一模糊，超采样后缩回）→ **均匀黑色遮罩**（整幅同一 alpha，默认 **0.3×255**）→ 纯白标题 → 五张底栏图。
- 底部**固定 5 张**竖条：预选 **`2.jpg`～`9.jpg` 最多 8 张**（与全库其它封面一致 **1+8=9 条**），按顺序**内容去重**后取前 5；与底图同内容的跳过；仍不足则用底图补齐。
- Emby 拉取：`1.jpg` 为媒体[0] 的 Fanart 链；`2～9.jpg` 为媒体[1]～[8] 的 Primary（见 `cover_emby_fetch`）。
- 左上标题：相对画布再收一档（约 **0.136H / 0.051H** × font_size）；中英行距约 **2 倍**；`margin_top` 由块高与 `row_y` 自动重算居中。
"""

from __future__ import annotations

import base64
import hashlib
import logging
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

logger = logging.getLogger(__name__)

CANVAS_W = 1920
CANVAS_H = 1080

# 标题字号略小于 style_single（底栏+标题区更挤，宜略收）
_SHELF_ZH_H_RATIO = 0.136
_SHELF_EN_H_RATIO = 0.051
_SHELF_ZH_SIZE_MIN = 42
_SHELF_EN_SIZE_MIN = 19
# 中英行距：相对原猜你喜欢/旧 shelf 约加倍
_SHELF_ZH_EN_GAP_MIN = 36
_SHELF_ZH_EN_GAP_H_RATIO = 0.060
_SHELF_GAP_EXTRA_EN = 1.10  # 原 0.55×2
_SHELF_GAP_EXTRA_ZH = 0.36  # 原 0.18×2


def _shelf_zh_en_line_gap(zh_line_h: int, en_line_h: int, zh_en_gap_base: int) -> int:
    return max(
        zh_en_gap_base,
        int(en_line_h * _SHELF_GAP_EXTRA_EN),
        int(zh_line_h * _SHELF_GAP_EXTRA_ZH),
    )


try:
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:
    _LANCZOS = Image.LANCZOS


def _split_equal_int(total: int, n: int) -> list[int]:
    if n <= 0:
        return []
    base = total // n
    rem = total % n
    return [base + (1 if i < rem else 0) for i in range(n)]


def _rounded_tile(img: Image.Image, radius: int) -> Image.Image:
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (w, h)], radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img.convert("RGBA"), (0, 0), mask)
    return out


def _composite_at(
    base: Image.Image, overlay: Image.Image, pos: tuple[int, int]
) -> Image.Image:
    b = base.convert("RGBA")
    layer = Image.new("RGBA", b.size, (0, 0, 0, 0))
    layer.paste(overlay, pos, overlay)
    return Image.alpha_composite(b, layer)


def _full_canvas_gaussian_blur(
    base_rgba: Image.Image,
    blur_radius: int = 26,
    scale: int = 3,
) -> Image.Image:
    """对整幅背景（已铺满画布）做高斯模糊：放大 → 模糊 → 缩回，无区域裁剪。"""
    w, h = base_rgba.size
    src = base_rgba.convert("RGB")
    big = src.resize((w * scale, h * scale), _LANCZOS)
    big_b = big.filter(ImageFilter.GaussianBlur(blur_radius))
    blurred = big_b.resize((w, h), _LANCZOS)
    return blurred.convert("RGBA")


def _black_vertical_mask(
    size: tuple[int, int],
    max_alpha_ratio: float = 0.3,
) -> Image.Image:
    """全画布纯黑半透明，**均匀** alpha（无纵向渐变），alpha = max_alpha_ratio×255。"""
    ww, hh = size
    max_a = max(0, min(255, int(255 * max_alpha_ratio)))
    alpha = Image.new("L", (ww, hh), max_a)
    z = Image.new("L", (ww, hh), 0)
    return Image.merge("RGBA", (z, z, z, alpha))


def _shelf_title_block_height(
    zh: str,
    title_en: str,
    zh_font: ImageFont.ImageFont,
    en_font: ImageFont.ImageFont,
    zh_en_gap_base: int,
) -> int:
    """中英标题块总高度；无英文时用拉丁占位字高，便于整块垂直居中（英文行占位不绘制）。"""
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    anchor = "lt"
    en_line = (title_en or "").strip().upper()
    en_for_layout = en_line if en_line else "Py"
    zh_bb = draw.textbbox((0, 0), zh, font=zh_font, anchor=anchor)
    zh_h = zh_bb[3] - zh_bb[1]
    en_bb0 = draw.textbbox((0, 0), en_for_layout, font=en_font, anchor=anchor)
    en_h = en_bb0[3] - en_bb0[1]
    gap = _shelf_zh_en_line_gap(zh_h, en_h, zh_en_gap_base)
    return zh_h + gap + en_h


def _plain_text_top_left(
    canvas_rgba: Image.Image,
    zh: str,
    en: str,
    zh_font: ImageFont.ImageFont,
    en_font: ImageFont.ImageFont,
    margin_left: int,
    margin_top: int,
    zh_en_gap: int,
) -> Image.Image:
    canvas = canvas_rgba.convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    en_line = en.strip().upper()
    en_for_layout = en_line if en_line else "Py"
    fill = (255, 255, 255, 255)
    anchor = "lt"

    zh_x, zh_y = margin_left, margin_top
    draw.text((zh_x, zh_y), zh, font=zh_font, fill=fill, anchor=anchor)
    zh_bb = draw.textbbox((zh_x, zh_y), zh, font=zh_font, anchor=anchor)
    en_bb0 = draw.textbbox((0, 0), en_for_layout, font=en_font, anchor=anchor)
    en_line_h = en_bb0[3] - en_bb0[1]
    zh_line_h = zh_bb[3] - zh_bb[1]
    gap = _shelf_zh_en_line_gap(zh_line_h, en_line_h, zh_en_gap)
    en_x, en_y = margin_left, zh_bb[3] + gap
    if en_line:
        draw.text((en_x, en_y), en_line, font=en_font, fill=fill, anchor=anchor)
    return canvas


def _find_background(folder: Path) -> Path | None:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = folder / f"1{ext}"
        if p.is_file():
            return p
    return None


def _collect_thumb_paths(folder: Path) -> list[Path]:
    """预选 2..9（最多 8 张），保持编号顺序。"""
    thumbs: list[Path] = []
    for i in range(2, 10):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = folder / f"{i}{ext}"
            if p.is_file():
                thumbs.append(p)
                break
    return thumbs


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _tile_paths_for_five_deduped(candidates: list[Path], bg_path: Path) -> list[Path]:
    """
    从最多 8 个预选文件中按顺序做**内容去重**，取 5 张底栏；不足 5 用底图补齐。
    与底图二进制相同的预选跳过，减少底栏与全屏背景同图。
    """
    bg_fp = _sha256_file(bg_path)
    seen_fp: set[str] = set()
    chosen: list[Path] = []
    for p in candidates:
        if len(chosen) >= 5:
            break
        fp = _sha256_file(p)
        if fp is None:
            continue
        if bg_fp is not None and fp == bg_fp:
            continue
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        chosen.append(p)
    while len(chosen) < 5:
        chosen.append(bg_path)
    return chosen[:5]


def _make_tile_rgba(
    source: Path,
    cell_w: int,
    cell_h: int,
    radius: int,
    fallbacks: list[Path],
) -> Image.Image | None:
    """按 source → fallbacks 顺序尝试解码，得到一张圆角 RGBA 小图。"""
    for pth in (source, *fallbacks):
        try:
            im = Image.open(pth).convert("RGB")
            fitted = ImageOps.fit(im, (cell_w, cell_h), method=Image.LANCZOS)
            return _rounded_tile(fitted, radius)
        except Exception as e:
            logger.warning("style_shelf_1: tile decode failed %s: %s", pth, e)
    return None


def _image_to_base64(image: Image.Image, quality: int = 90) -> str:
    buffer = BytesIO()
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    try:
        image.save(buffer, format="WEBP", quality=quality, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception:
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")


def create_style_shelf_1(
    library_dir: str,
    title: tuple[str, str],
    font_path: tuple[str, str],
    font_size: tuple[float, float] = (1.0, 1.0),
) -> str | bool:
    """
    :param library_dir: 目录内含 1.*（背景）及可选 2..9 预选（最多 8 张，与其它样式共 9 条）；底栏**始终渲染 5 张**（内容去重后选取）。
    :param title: (title_zh, title_en)
    :param font_path: (zh_font_path, en_font_path)
    """
    try:
        zh_font_path, en_font_path = font_path
        title_zh, title_en = title
        zh_ratio, en_ratio = float(font_size[0] or 1.0), float(font_size[1] or 1.0)

        folder = Path(library_dir)
        bg_path = _find_background(folder)
        if not bg_path:
            logger.error("style_shelf_1: missing background 1.* in %s", library_dir)
            return False

        w, h = CANVAS_W, CANVAS_H
        bg = Image.open(bg_path).convert("RGB")
        canvas = ImageOps.fit(bg, (w, h), method=Image.LANCZOS).convert("RGBA")
        canvas = _full_canvas_gaussian_blur(canvas, blur_radius=13, scale=3)

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

        # 整幅均匀黑色遮罩；标题与底栏在遮罩之上
        canvas = Image.alpha_composite(canvas, _black_vertical_mask((w, h), max_alpha_ratio=0.3))
        x_positions: list[int] = []
        acc = gaps[0]
        for i in range(5):
            x_positions.append(acc)
            acc += cell_w + gaps[i + 1]

        thumbs = _collect_thumb_paths(folder)
        tile_paths = _tile_paths_for_five_deduped(thumbs, bg_path)
        # 去重 fallback 顺序：背景优先，其余 thumb 作备选
        fb_unique: list[Path] = []
        for p in (bg_path, *thumbs):
            if p not in fb_unique:
                fb_unique.append(p)

        tiles_rgba: list[Image.Image] = []
        for slot in range(5):
            tile = _make_tile_rgba(
                tile_paths[slot], cell_w, cell_h, radius, fallbacks=fb_unique
            )
            if tile is None:
                logger.error("style_shelf_1: cannot build bottom tile slot %d", slot)
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
        # 标题块垂直居中于「画布顶」与「底栏顶」之间；仅中文时仍按双行占位算高度
        margin_top = max(int(h * 0.02), (row_y - block_h) // 2)
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

        return _image_to_base64(canvas)
    except Exception as e:
        logger.error("style_shelf_1 failed: %s", e, exc_info=True)
        return False
