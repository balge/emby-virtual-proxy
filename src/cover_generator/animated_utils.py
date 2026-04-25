import base64
from io import BytesIO
from typing import Iterable

from PIL import Image


def pil_image_from_base64(data: str) -> Image.Image:
    raw = base64.b64decode(data)
    return Image.open(BytesIO(raw)).convert("RGBA")


def encode_gif_base64(frames: Iterable[Image.Image], fps: int = 12) -> str:
    rgba_frames = [f.convert("RGBA") for f in frames]
    if not rgba_frames:
        raise ValueError("No frames to encode")
    # Use one global palette for all frames to avoid per-frame palette drift,
    # which causes visible blocky bands on smooth gradient backgrounds.
    palette_seed = rgba_frames[0].convert("RGB").quantize(
        colors=256, method=Image.Quantize.MEDIANCUT
    )
    frame_list = [
        f.convert("RGB").quantize(palette=palette_seed, dither=Image.Dither.FLOYDSTEINBERG)
        for f in rgba_frames
    ]
    duration = max(20, int(1000 / max(1, int(fps))))
    buf = BytesIO()
    frame_list[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frame_list[1:],
        duration=duration,
        loop=0,
        optimize=False,
        disposal=2,
    )
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def encode_apng_base64(frames: Iterable[Image.Image], fps: int = 12) -> str:
    frame_list = [f.convert("RGBA") for f in frames]
    if not frame_list:
        raise ValueError("No frames to encode")
    duration = max(20, int(1000 / max(1, int(fps))))
    buf = BytesIO()
    frame_list[0].save(
        buf,
        format="PNG",
        save_all=True,
        append_images=frame_list[1:],
        duration=duration,
        loop=0,
        optimize=False,
        disposal=2,
    )
    return base64.b64encode(buf.getvalue()).decode("utf-8")
