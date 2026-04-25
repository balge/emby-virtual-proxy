import logging
from pathlib import Path

from PIL import Image

from .animated_utils import encode_apng_base64, encode_gif_base64, pil_image_from_base64
from .style_single_2 import create_style_single_2

logger = logging.getLogger(__name__)


def _list_images(folder: Path) -> list[Path]:
    exts = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
    return sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts])


def create_style_single_2_animated(
    library_dir,
    title,
    font_path,
    font_size=(1, 1.2),
    animation_duration=8,
    animation_fps=12,
    animation_format="gif",
    output_width=400,
    image_count=6,
):
    try:
        folder = Path(library_dir)
        images = _list_images(folder)[: max(2, int(image_count))]
        if len(images) < 2:
            logger.warning("style_single_2_animated: not enough source images")
            return False

        keyframes = []
        for p in images:
            b64 = create_style_single_2(str(p), title, font_path, font_size=font_size)
            if b64:
                keyframes.append(pil_image_from_base64(b64))
        if len(keyframes) < 2:
            return False

        fps = max(1, int(animation_fps))
        duration = max(2, int(animation_duration))
        total_frames = max(12, fps * duration)
        seg = max(1, total_frames // len(keyframes))
        target_w = max(120, int(output_width or 400))
        frames = []
        for i, current in enumerate(keyframes):
            nxt = keyframes[(i + 1) % len(keyframes)]
            w, h = current.size
            for step in range(seg):
                t = step / float(seg)
                frame = Image.blend(current, nxt, t)
                # gentle zoom pulse
                zoom = 1.0 + 0.015 * (1.0 - abs(2 * t - 1))
                sw, sh = int(w * zoom), int(h * zoom)
                scaled = frame.resize((sw, sh), Image.Resampling.BICUBIC)
                left = (sw - w) // 2
                top = (sh - h) // 2
                rgba = scaled.crop((left, top, left + w, top + h)).convert("RGBA")
                w0, h0 = rgba.size
                if w0 > 0 and w0 != target_w:
                    h1 = max(1, int(h0 * (target_w / float(w0))))
                    rgba = rgba.resize((target_w, h1), Image.Resampling.BICUBIC)
                frames.append(rgba)

        fmt = str(animation_format or "gif").lower()
        if fmt == "apng":
            return encode_apng_base64(frames, fps=fps)
        return encode_gif_base64(frames, fps=fps)
    except Exception as e:
        logger.error(f"create_style_single_2_animated failed: {e}")
        return False
