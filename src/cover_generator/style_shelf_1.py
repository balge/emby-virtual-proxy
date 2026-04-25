"""
Shelf-style library cover（样式四）：背景 + 底栏五海报 + 左上标题。

布局与图层与「猜你喜欢」脚本一致：
- 画布 1920×1080；`1.*` 全幅铺满；整宽上 6 段等宽缝 + 5 张等宽竖条（2:3）、圆角、无阴影。
- 自下而上：背景 → **整幅高斯虚化**（全画布同一模糊，超采样后缩回）→ **均匀黑色遮罩**（整幅同一 alpha，默认 **0.3×255**）→ 纯白标题 → 五张底栏图。
- 九槽各 **Primary**（`n.*`）与可选 **宽屏**（`fanart_n.*`）。**有 `fanart_*` 时**在候选里按四角复杂度选底图；**若九槽均无 Fanart/Backdrop 文件**，则用 **槽 1 的 Primary** 作全屏虚化底图。底栏仍为其余槽 Primary 顺序去重五格。
- Emby：见 `cover_emby_fetch`。
- 左上标题：相对画布再收一档（约 **0.136H / 0.051H** × font_size）；中英行距约 **2 倍**；`margin_top` 由块高与 `row_y` 自动重算居中。
"""

from __future__ import annotations

import base64
import hashlib
import logging
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

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
    blur_radius: int = 13,
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


def _shelf_patch_complexity(patch_rgb: Image.Image) -> float:
    """单块 RGB 子图：边缘能量 + 色彩分散；越低越「平」。子图过大时先缩再放缩后统计。"""
    im = patch_rgb
    w, h = im.size
    if w < 2 or h < 2:
        return 1e9
    max_side = 480
    if max(w, h) > max_side:
        scale = max_side / float(max(w, h))
        im = im.resize((max(2, int(w * scale)), max(2, int(h * scale))), _LANCZOS)
    gray = im.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    stat_e = ImageStat.Stat(edges)
    edge_mean = float(stat_e.mean[0]) if stat_e.mean else 0.0
    stat_rgb = ImageStat.Stat(im)
    spreads = [float(s) for s in stat_rgb.stddev]
    color_spread = sum(spreads) / max(1, len(spreads))
    return edge_mean * 1.15 + color_spread * 0.18


def _shelf_bg_complexity_score(path: Path) -> float:
    """
    越低越适合作虚化全屏背景：在全图（超大图先等比缩到长边至多 2160）上取四角，
    每角矩形 **宽 = 全宽/4、高 = 全高/5**，四角子图各算 `_shelf_patch_complexity`，再取算术平均。
    """
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return 1e9
    w0, h0 = im.size
    if w0 < 4 or h0 < 5:
        return 1e9
    work_max = 2160
    if max(w0, h0) > work_max:
        sc = work_max / float(max(w0, h0))
        w = max(4, int(w0 * sc))
        h = max(5, int(h0 * sc))
        im = im.resize((w, h), _LANCZOS)
    else:
        w, h = w0, h0

    cw = max(1, w // 4)
    ch = max(1, h // 5)
    boxes = (
        (0, 0, cw, ch),
        (w - cw, 0, w, ch),
        (0, h - ch, cw, h),
        (w - cw, h - ch, w, h),
    )
    parts: list[float] = []
    for box in boxes:
        parts.append(_shelf_patch_complexity(im.crop(box)))
    return sum(parts) / float(len(parts))


def _collect_fanart_pool(folder: Path) -> list[tuple[int, Path]]:
    """Fanart 槽 fanart_1..fanart_9，仅已有文件（无 Fanart 的媒体不在此列）。"""
    out: list[tuple[int, Path]] = []
    for i in range(1, 10):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = folder / f"fanart_{i}{ext}"
            if p.is_file():
                out.append((i, p))
                break
    return out


def _collect_primary_pool(folder: Path) -> list[tuple[int, Path]]:
    """Primary 槽 1..9，按编号顺序。"""
    out: list[tuple[int, Path]] = []
    for i in range(1, 10):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = folder / f"{i}{ext}"
            if p.is_file():
                out.append((i, p))
                break
    return out


def _primary_strip_candidates_ordered(
    primary_by_slot: dict[int, Path],
    bg_slot: int | None = None,
) -> list[Path]:
    """
    按槽位 **1→9** 固定顺序收集 Primary。
    注意：为支持“背景图与槽位可重复展示”，不再排除背景槽位。
    """
    del bg_slot
    out: list[Path] = []
    for slot in range(1, 10):
        p = primary_by_slot.get(slot)
        if p is not None:
            out.append(p)
    return out


def _pick_fanart_background(
    fanart_pool: list[tuple[int, Path]],
) -> tuple[Path | None, int, float]:
    """仅从 Fanart 候选中选复杂度最低的一张作全屏底图。返回 (path, slot_1based, score)。"""
    if not fanart_pool:
        return None, 0, 0.0
    scored: list[tuple[int, Path, float]] = [
        (slot, pth, _shelf_bg_complexity_score(pth)) for slot, pth in fanart_pool
    ]
    scored.sort(key=lambda x: (x[2], x[0]))
    bg_slot, bg_path, score = scored[0]
    logger.info(
        "style_shelf_1: Fanart background slot %s (complexity %.2f, fanart_candidates=%d)",
        bg_slot,
        score,
        len(fanart_pool),
    )
    return bg_path, bg_slot, score


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _tile_paths_for_five_deduped(candidates: list[Path], bg_path: Path) -> list[Path]:
    """
    输入为 Primary 候选（按槽位顺序）；沿序取**内容不重复**的前 5 张底栏；
    与背景图允许重复展示（不再跳过同字节）；仍不足 5 则用底图文件路径补齐。
    """
    seen_fp: set[str] = set()
    chosen: list[Path] = []
    for p in candidates:
        if len(chosen) >= 5:
            break
        fp = _sha256_file(p)
        if fp is None:
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
    :param library_dir: 九槽 `n.*`（Primary）与可选 `fanart_n.*`；无任一张宽屏图时用槽 1 Primary 作背景；底栏五格由其余槽 Primary 顺序去重。
    :param title: (title_zh, title_en)
    :param font_path: (zh_font_path, en_font_path)
    """
    try:
        zh_font_path, en_font_path = font_path
        title_zh, title_en = title
        zh_ratio, en_ratio = float(font_size[0] or 1.0), float(font_size[1] or 1.0)

        folder = Path(library_dir)
        fanart_pool = _collect_fanart_pool(folder)
        primary_pool = _collect_primary_pool(folder)
        primary_by_slot = {s: p for s, p in primary_pool}
        if fanart_pool:
            bg_path, bg_slot, _score = _pick_fanart_background(fanart_pool)
            if not bg_path:
                return False
        else:
            # 九槽均无 Fanart/Backdrop：用第一个媒体（槽 1）的 Primary 作全屏底图
            bg_path = primary_by_slot.get(1)
            if not bg_path:
                logger.error(
                    "style_shelf_1: no fanart_* and no Primary for slot 1 in %s",
                    library_dir,
                )
                return False
            bg_slot = 1
            logger.info(
                "style_shelf_1: no Fanart/Backdrop files; using slot 1 Primary as background"
            )
        # 剩下 8 个槽：严格按槽位 1→9 顺序取 Primary，再交给去重逻辑填五格
        thumbs = _primary_strip_candidates_ordered(primary_by_slot, bg_slot)

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

        tile_paths = _tile_paths_for_five_deduped(thumbs, bg_path)
        # 解码 fallback：底图 Fanart 优先，再 Primary 槽位序、Fanart 其余槽
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
