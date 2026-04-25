"""
Shelf-style library cover（样式四）：背景 + 底栏五海报 + 左上标题。

布局与图层与「猜你喜欢」脚本一致：
- 画布 1920×1080；`1.*` 全幅铺满；整宽上 6 段等宽缝 + 5 张等宽竖条（2:3）、圆角、无阴影。
- 自下而上：背景 → 黑色纵向蒙版 → 纯白标题（无描边/阴影）→ 五张底栏图。
- 底部**固定 5 张**竖条：`2..9` 从左到右取用，不足 5 张则循环补齐；无 `2..` 时五格均用背景图。
- Emby 拉取（与 admin `cover_emby_fetch` 一致）：`1.jpg` 为首条 Fanart→Art→Backdrop→Primary；`2～6.jpg` 为连续 5 条 Primary。
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger(__name__)

CANVAS_W = 1920
CANVAS_H = 1080

try:
    _RESAMPLE_NEAREST = Image.Resampling.NEAREST
except AttributeError:
    _RESAMPLE_NEAREST = Image.NEAREST


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


def _black_vertical_mask(size: tuple[int, int], bottom_strength: float = 0.58) -> Image.Image:
    ww, hh = size
    col = Image.new("L", (1, hh))
    cpix = col.load()
    denom = max(hh - 1, 1)
    for y in range(hh):
        t = (y / denom) ** 1.12
        if t > 1.0:
            t = 1.0
        cpix[0, y] = int(255 * bottom_strength * t)
    alpha = col.resize((ww, hh), _RESAMPLE_NEAREST)
    z = Image.new("L", (ww, hh), 0)
    return Image.merge("RGBA", (z, z, z, alpha))


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
    fill = (255, 255, 255, 255)
    anchor = "lt"

    zh_x, zh_y = margin_left, margin_top
    draw.text((zh_x, zh_y), zh, font=zh_font, fill=fill, anchor=anchor)
    zh_bb = draw.textbbox((zh_x, zh_y), zh, font=zh_font, anchor=anchor)
    en_bb0 = draw.textbbox((0, 0), en_line, font=en_font, anchor=anchor)
    en_line_h = en_bb0[3] - en_bb0[1]
    zh_line_h = zh_bb[3] - zh_bb[1]
    gap = max(
        zh_en_gap,
        int(en_line_h * 0.55),
        int(zh_line_h * 0.18),
    )
    en_x, en_y = margin_left, zh_bb[3] + gap
    draw.text((en_x, en_y), en_line, font=en_font, fill=fill, anchor=anchor)
    return canvas


def _find_background(folder: Path) -> Path | None:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = folder / f"1{ext}"
        if p.is_file():
            return p
    return None


def _collect_thumb_paths(folder: Path) -> list[Path]:
    thumbs: list[Path] = []
    for i in range(2, 10):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = folder / f"{i}{ext}"
            if p.is_file():
                thumbs.append(p)
                break
    return thumbs


def _tile_paths_for_five(thumbs: list[Path], bg_path: Path) -> list[Path]:
    """恰好 5 个路径：槽位与底栏一一对应，素材不足则按序循环。"""
    if not thumbs:
        return [bg_path] * 5
    paths = list(thumbs[:5])
    while len(paths) < 5:
        paths.append(thumbs[len(paths) % len(thumbs)])
    return paths[:5]


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
    :param library_dir: 目录内含 1.*（背景）及可选 2..9 素材；底栏**始终渲染 5 张**。
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
        canvas = Image.alpha_composite(canvas, _black_vertical_mask((w, h), bottom_strength=0.58))

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

        thumbs = _collect_thumb_paths(folder)
        tile_paths = _tile_paths_for_five(thumbs, bg_path)
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

        zh_size = max(48, int(h * 0.10 * zh_ratio))
        en_size = max(22, int(h * 0.036 * en_ratio))
        try:
            zh_font = ImageFont.truetype(zh_font_path, zh_size)
        except Exception:
            zh_font = ImageFont.load_default()
        try:
            en_font = ImageFont.truetype(en_font_path, en_size)
        except Exception:
            en_font = ImageFont.load_default()

        margin_left = int(w * 0.042)
        margin_top = int(h * 0.066)
        zh_en_gap = max(22, int(h * 0.030))
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
