"""
Short-lived subprocess entry: imports cover_generator + Pillow only here so the
main server process does not retain their memory after each cover job.

Run: python -m cover_worker <path-to-job.json>
"""

from __future__ import annotations

import base64
import importlib
import json
import sys
import traceback
from io import BytesIO
from pathlib import Path

from PIL import Image

OUTPUT_WIDTH = 400

ALLOWED_STYLES = frozenset(
    {
        "style_multi_1",
        "style_shelf_1",
        "style_shelf_1_animated",
        "style_single_1",
        "style_single_2",
        "style_multi_1_animated",
        "style_single_1_animated",
        "style_single_2_animated",
    }
)


def _build_kwargs(job: dict) -> dict:
    style_name = job["style_name"]
    library_dir = Path(job["library_dir"])
    kwargs: dict = {
        "title": (job["title_zh"], job["title_en"]),
        "font_path": (job["zh_font_path"], job["en_font_path"]),
        "font_size": (1, 1.2),
    }
    if "animation_duration" in job:
        kwargs["animation_duration"] = int(job["animation_duration"])
    if "animation_fps" in job:
        kwargs["animation_fps"] = int(job["animation_fps"])
    if "animation_format" in job:
        kwargs["animation_format"] = str(job["animation_format"])
    if "animated_image_count" in job:
        kwargs["image_count"] = int(job["animated_image_count"])
    if "animated_departure_type" in job:
        kwargs["departure_type"] = str(job["animated_departure_type"])
    if "animated_scroll_direction" in job:
        kwargs["scroll_direction"] = str(job["animated_scroll_direction"])
    if style_name.endswith("_animated"):
        kwargs["output_width"] = int(job.get("output_width", 400) or 300)
    if style_name in ("style_shelf_1", "style_shelf_1_animated"):
        # shelf 与 multi 共用 worker 时不宜对英文用 1.2×；整体再收一档标题
        kwargs["font_size"] = (0.86, 0.94)
    if "output_width" in job:
        kwargs["output_width"] = int(job["output_width"] or OUTPUT_WIDTH)
    if style_name in ("style_multi_1", "style_shelf_1", "style_multi_1_animated", "style_shelf_1_animated"):
        kwargs["library_dir"] = str(library_dir)
    elif style_name in ("style_single_1", "style_single_2"):
        main_image_path = library_dir / "1.jpg"
        if not main_image_path.is_file():
            raise FileNotFoundError(f"main image not found: {main_image_path}")
        kwargs["image_path"] = str(main_image_path)
    elif style_name in ("style_single_1_animated", "style_single_2_animated"):
        kwargs["library_dir"] = str(library_dir)
    else:
        raise ValueError(f"unknown style: {style_name}")
    return kwargs


def _finalize_image(image_data: bytes, output_path: str, crop_16_9: bool, output_format: str = "jpeg") -> None:
    fmt = (output_format or "jpeg").lower()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if fmt in ("gif", "apng", "png"):
        # 动图在样式生成阶段已按目标尺寸输出，这里不再做二次缩放。
        Path(output_path).write_bytes(image_data)
        return
    def _resize_keep_aspect(src: Image.Image) -> Image.Image:
        w, h = src.size
        if w <= 0 or h <= 0:
            return src
        if w == OUTPUT_WIDTH:
            return src
        nh = max(1, int(h * (OUTPUT_WIDTH / float(w))))
        return src.resize((OUTPUT_WIDTH, nh), Image.Resampling.LANCZOS)
    img = Image.open(BytesIO(image_data))
    try:
        if img.mode != "RGB":
            img = img.convert("RGB")
        if crop_16_9:
            w, h = img.size
            target_ratio = 16 / 9
            current_ratio = w / h
            if current_ratio > target_ratio:
                new_w = int(h * target_ratio)
                left = (w - new_w) // 2
                img = img.crop((left, 0, left + new_w, h))
            elif current_ratio < target_ratio:
                new_h = int(w / target_ratio)
                top = (h - new_h) // 2
                img = img.crop((0, top, w, top + new_h))
        img = _resize_keep_aspect(img)
        img.save(output_path, "JPEG", quality=90)
    finally:
        img.close()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m cover_worker <job.json>", file=sys.stderr)
        return 2
    job_path = Path(sys.argv[1])
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"invalid job file: {e}", file=sys.stderr)
        return 2

    style_name = job.get("style_name")
    if style_name not in ALLOWED_STYLES:
        print(f"invalid style_name: {style_name!r}", file=sys.stderr)
        return 1

    for key in ("title_zh", "title_en", "zh_font_path", "en_font_path", "library_dir", "output_path"):
        if key not in job:
            print(f"missing job key: {key}", file=sys.stderr)
            return 2

    try:
        style_module = importlib.import_module(f"cover_generator.{style_name}")
        create_function = getattr(style_module, f"create_{style_name}")
        kwargs = _build_kwargs(job)
        res = create_function(**kwargs)
        if not res:
            print("create_function returned empty", file=sys.stderr)
            return 1
        raw = base64.b64decode(res)
        crop = bool(job.get("crop_16_9", False))
        _finalize_image(raw, job["output_path"], crop, str(job.get("output_format", "jpeg")))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
