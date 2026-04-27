import logging
from pathlib import Path

from PIL import Image

from .animated_utils import encode_apng_base64, encode_gif_base64, pil_image_from_base64
from .style_single_1 import create_style_single_1

logger = logging.getLogger(__name__)


def _list_images(folder: Path) -> list[Path]:
    exts = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
    return sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts])


def _resize_to_width(img: Image.Image, target_w: int) -> Image.Image:
    rgba = img.convert("RGBA")
    w, h = rgba.size
    if w <= 0 or h <= 0 or w == target_w:
        return rgba
    target_h = max(1, int(h * (target_w / float(w))))
    return rgba.resize((target_w, target_h), Image.Resampling.BICUBIC)


def create_style_single_1_animated(
    library_dir,
    title,
    font_path,
    font_size=(1, 1.2),
    animation_duration=8,
    animation_fps=12,
    animation_format="gif",
    output_width=400,
    image_count=6,
    departure_type="fly",
):
    try:
        folder = Path(library_dir)
        images = _list_images(folder)[: max(2, int(image_count))]
        if len(images) < 2:
            logger.warning("style_single_1_animated: not enough source images")
            return False

        target_w = max(120, int(output_width or 400))
        keyframes: list[Image.Image] = []
        for p in images:
            b64 = create_style_single_1(str(p), title, font_path, font_size=font_size)
            if not b64:
                continue
            keyframes.append(_resize_to_width(pil_image_from_base64(b64), target_w))
        if len(keyframes) < 2:
            return False

        fps = max(1, int(animation_fps))
        duration = max(2, int(animation_duration))
        total_frames = max(12, fps * duration)
        seg = max(1, total_frames // len(keyframes))

        frames: list[Image.Image] = []
        for i, current in enumerate(keyframes):
            nxt = keyframes[(i + 1) % len(keyframes)]
            w, h = current.size
            for step in range(seg):
                t = step / float(seg)
                if departure_type == "fade":
                    frame = Image.blend(current, nxt, t)
                elif departure_type == "crossfade":
                    # Slight zoom while crossfading to mimic curtain motion.
                    zoom = 1.0 + 0.02 * t
                    sw, sh = int(w * zoom), int(h * zoom)
                    scaled = nxt.resize((sw, sh), Image.Resampling.BICUBIC)
                    left = (sw - w) // 2
                    top = (sh - h) // 2
                    cropped = scaled.crop((left, top, left + w, top + h))
                    frame = Image.blend(current, cropped, t)
                else:
                    # fly: current moves left and next flies in from right
                    dx = int(w * t)
                    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                    layer.paste(current, (-dx, 0), current)
                    layer.paste(nxt, (w - dx, 0), nxt)
                    frame = layer
                frames.append(frame.convert("RGBA"))

        fmt = str(animation_format or "gif").lower()
        if fmt == "apng":
            return encode_apng_base64(frames, fps=fps)
        return encode_gif_base64(frames, fps=fps)
    except Exception as e:
        logger.error(f"create_style_single_1_animated failed: {e}")
        return False
