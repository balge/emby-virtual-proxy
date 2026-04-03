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

ALLOWED_STYLES = frozenset({"style_multi_1", "style_single_1", "style_single_2"})


def _build_kwargs(job: dict) -> dict:
    style_name = job["style_name"]
    library_dir = Path(job["library_dir"])
    kwargs: dict = {
        "title": (job["title_zh"], job["title_en"]),
        "font_path": (job["zh_font_path"], job["en_font_path"]),
        "font_size": (1, 1.2),
    }
    if style_name == "style_multi_1":
        kwargs["library_dir"] = str(library_dir)
    elif style_name in ("style_single_1", "style_single_2"):
        main_image_path = library_dir / "1.jpg"
        if not main_image_path.is_file():
            raise FileNotFoundError(f"main image not found: {main_image_path}")
        kwargs["image_path"] = str(main_image_path)
    else:
        raise ValueError(f"unknown style: {style_name}")
    return kwargs


def _finalize_image(image_data: bytes, output_path: str, crop_16_9: bool) -> None:
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
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
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
        _finalize_image(raw, job["output_path"], crop)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
