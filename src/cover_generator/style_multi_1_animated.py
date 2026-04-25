from __future__ import annotations

import logging
import math
import os
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .animated_utils import encode_apng_base64, encode_gif_base64
from .style_multi_1 import (
    POSTER_GEN_CONFIG,
    add_shadow,
    create_blur_background,
    create_gradient_background,
    darken_color,
    draw_color_block,
    draw_multiline_text_on_image,
    draw_text_on_image,
    find_dominant_vibrant_colors,
    get_poster_primary_color,
)

logger = logging.getLogger(__name__)


def _first_frame_source(folder: Path) -> Path | None:
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        p = folder / f"1{ext}"
        if p.is_file():
            return p
    return None


def create_style_multi_1_animated(
    library_dir,
    title,
    font_path,
    font_size=(1, 1.2),
    is_blur=False,
    blur_size=50,
    color_ratio=0.8,
    animation_duration=8,
    animation_fps=24,
    animation_format="gif",
    output_width=400,
    scroll_direction="alternate",
    image_count=None,
    departure_type=None,
):
    del image_count, departure_type
    try:
        zh_ratio, en_ratio = font_size
        zh_ratio = max(1.0, float(zh_ratio))
        en_ratio = max(1.0, float(en_ratio))
        blur_size = max(0, int(blur_size))
        color_ratio = min(1.0, max(0.0, float(color_ratio)))

        poster_folder = Path(library_dir)
        title_zh, title_en = title
        zh_font_path, en_font_path = font_path

        first_image_path = _first_frame_source(poster_folder)
        if not first_image_path:
            logger.warning("style_multi_1_animated: missing slot-1 source image")
            return False

        rows = POSTER_GEN_CONFIG["ROWS"]
        cols = POSTER_GEN_CONFIG["COLS"]
        margin = float(POSTER_GEN_CONFIG["MARGIN"])
        corner_radius = float(POSTER_GEN_CONFIG["CORNER_RADIUS"])
        rotation_angle = float(POSTER_GEN_CONFIG["ROTATION_ANGLE"])
        start_x = float(POSTER_GEN_CONFIG["START_X"])
        start_y = float(POSTER_GEN_CONFIG["START_Y"])
        column_spacing = float(POSTER_GEN_CONFIG["COLUMN_SPACING"])
        cell_width = float(POSTER_GEN_CONFIG["CELL_WIDTH"])
        cell_height = float(POSTER_GEN_CONFIG["CELL_HEIGHT"])
        target_w = max(120, int(output_width or 400))
        target_h = max(68, int(target_w * (POSTER_GEN_CONFIG["CANVAS_HEIGHT"] / POSTER_GEN_CONFIG["CANVAS_WIDTH"])))

        # 跟参考 style_animated_3 一致：采用目标分辨率缩放坐标系统
        scale = target_h / 1080.0

        def s(val: float) -> float:
            return float(val) * scale

        margin_s = s(margin)
        corner_radius_s = s(corner_radius)
        start_x_s = s(start_x)
        start_y_s = s(start_y)
        column_spacing_s = s(column_spacing)
        cell_width_s = s(cell_width)
        cell_height_s = s(cell_height)
        zh_font_size_s = int(163 * zh_ratio * scale)
        en_font_size_s = int(50 * en_ratio * scale)

        # 1) 背景+文字预合成
        color_img = Image.open(first_image_path).convert("RGB")
        vibrant = find_dominant_vibrant_colors(color_img)
        blur_color = vibrant[0] if vibrant else (237, 159, 77)
        gradient_color = get_poster_primary_color(str(first_image_path))
        if is_blur:
            bg = create_blur_background(
                str(first_image_path),
                target_w,
                target_h,
                blur_color,
                max(1, int(blur_size * scale)),
                color_ratio,
            )
        else:
            bg = create_gradient_background(target_w, target_h, gradient_color)
        text_shadow = darken_color(blur_color, 0.8)
        random_color = (
            vibrant[1]
            if len(vibrant) > 1
            else (
                random.randint(50, 200),
                random.randint(50, 200),
                random.randint(50, 200),
                255,
            )
        )

        text_overlay = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
        text_overlay = draw_text_on_image(
            text_overlay,
            title_zh,
            (s(73.32), s(427.34)),
            zh_font_path,
            "ch.ttf",
            zh_font_size_s,
            shadow=is_blur,
            shadow_color=text_shadow,
        )
        if title_en:
            text_overlay, line_count = draw_multiline_text_on_image(
                text_overlay,
                title_en,
                (s(124.68), s(624.55)),
                en_font_path,
                "en.ttf",
                en_font_size_s,
                s(en_font_size_s * 0.1),
                shadow=is_blur,
                shadow_color=text_shadow,
            )
            cb_h = int(
                en_font_size_s
                + s(en_font_size_s * 0.1)
                + (line_count - 1) * (en_font_size_s + s(en_font_size_s * 0.1))
            )
            text_overlay = draw_color_block(
                text_overlay, (s(84.38), s(620.06)), (s(21.51), cb_h), random_color
            )
        base_frame = Image.alpha_composite(bg.convert("RGBA"), text_overlay)

        # 2) 按参考顺序加载 9 张图（不足时循环补齐）
        supported = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")
        custom_order = "315426987"
        order_map = {num: idx for idx, num in enumerate(custom_order)}
        all_posters = sorted(
            [
                os.path.join(poster_folder, f)
                for f in os.listdir(poster_folder)
                if os.path.isfile(os.path.join(poster_folder, f))
                and f.lower().endswith(supported)
                and os.path.splitext(f)[0] in order_map
            ],
            key=lambda p: order_map[os.path.splitext(os.path.basename(p))[0]],
        )
        if not all_posters:
            return False
        extended = []
        while len(extended) < rows * cols:
            extended.extend(all_posters)
        extended = extended[: rows * cols]

        processed = []
        for p in extended:
            try:
                img = Image.open(p).convert("RGBA")
                img = ImageOps.fit(
                    img,
                    (int(cell_width_s), int(cell_height_s)),
                    method=Image.Resampling.BILINEAR,
                )
                if corner_radius_s > 0:
                    mask = Image.new("L", (int(cell_width_s), int(cell_height_s)), 0)
                    ImageDraw.Draw(mask).rounded_rectangle(
                        [(0, 0), (int(cell_width_s), int(cell_height_s))],
                        radius=corner_radius_s,
                        fill=255,
                    )
                    img.putalpha(mask)
                img = add_shadow(
                    img,
                    offset=(int(s(15)), int(s(15))),
                    shadow_color=(0, 0, 0, 200),
                    blur_radius=max(1, int(s(10))),
                )
                processed.append(img)
            except Exception as e:
                logger.error("style_multi_1_animated preprocess failed: %s", e)

        if not processed:
            return False

        # 3) 三列长条（未旋转） + 滚动参数
        column_posters = [processed[i::cols] for i in range(cols)]
        scroll_dist = rows * (cell_height_s + margin_s)
        if scroll_dist <= 0:
            return False
        cos_val = max(1e-6, math.cos(math.radians(abs(rotation_angle))))
        view_h = int(target_h / cos_val * 1.6)
        view_w = int(cell_width_s + s(60))

        rendered_strips = []
        for col_imgs in column_posters:
            loop_posters = col_imgs * 2 + [col_imgs[0]]
            shadow_extra = int(s(60))
            strip_h = len(loop_posters) * cell_height_s + (len(loop_posters) - 1) * margin_s
            strip = Image.new(
                "RGBA",
                (int(cell_width_s) + shadow_extra, int(strip_h) + shadow_extra),
                (0, 0, 0, 0),
            )
            for idx, pimg in enumerate(loop_posters):
                strip.paste(pimg, (0, int(idx * (cell_height_s + margin_s))), pimg)
            rendered_strips.append(strip)

        # 4) 对齐静态布局中心（参考 style_animated_3）
        base_centers = []
        col_x_step = cell_width_s - s(50)
        # 与静态 style_multi_1 一致：右列额外偏移应为 +60（再叠加 2*(cell_w-50)）
        third_col_extra_x = s(60)
        for ci in range(cols):
            cx = start_x_s + ci * column_spacing_s
            cy = start_y_s + (rows * cell_height_s + (rows - 1) * margin_s) // 2
            if ci == 1:
                cx += col_x_step
            elif ci == 2:
                cy += -s(155)
                cx += col_x_step * 2 + third_col_extra_x
            base_centers.append((cx, cy))

        # 5) 逐帧合成（方向逻辑按参考）
        mode = (scroll_direction or "alternate").lower()
        fps = max(1, int(animation_fps))
        duration = max(2, int(animation_duration))
        n_frames = min(500, max(12, fps * duration))
        col_phases = [0, scroll_dist // 4, scroll_dist // 2]

        frames = []
        for i in range(n_frames):
            frame = base_frame.copy()
            progress = i / float(n_frames)
            total_scroll = progress * scroll_dist
            for col_index, strip in enumerate(rendered_strips):
                phase_offset = col_phases[col_index % len(col_phases)]
                if mode == "up":
                    dy_float = total_scroll % scroll_dist
                elif mode == "down":
                    dy_float = (scroll_dist - total_scroll) % scroll_dist
                elif mode == "alternate":
                    if col_index == 1:
                        dy_float = (total_scroll + phase_offset) % scroll_dist
                    else:
                        dy_float = (scroll_dist - total_scroll + phase_offset) % scroll_dist
                elif mode == "alternate_reverse":
                    if col_index == 1:
                        dy_float = (scroll_dist - total_scroll + phase_offset) % scroll_dist
                    else:
                        dy_float = (total_scroll + phase_offset) % scroll_dist
                else:
                    dy_float = (scroll_dist - total_scroll) % scroll_dist

                dy_int = int(dy_float)
                sub_strip = strip.crop((0, dy_int, view_w, dy_int + view_h))
                rotated_piece = sub_strip.rotate(
                    rotation_angle,
                    resample=Image.Resampling.BILINEAR,
                    expand=True,
                )
                bcx, bcy = base_centers[col_index]
                pos_x = int(bcx - rotated_piece.width // 2 + cell_width_s // 2)
                pos_y = int(bcy - rotated_piece.height // 2)
                frame.paste(rotated_piece, (pos_x, pos_y), rotated_piece)
            frames.append(frame)

        fmt = str(animation_format or "gif").lower()
        if fmt == "apng":
            return encode_apng_base64(frames, fps=fps)
        return encode_gif_base64(frames, fps=fps)
    except Exception as e:
        logger.error("create_style_multi_1_animated failed: %s", e, exc_info=True)
        return False
