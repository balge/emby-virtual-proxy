"""
Invoke cover_worker in a separate interpreter process; exits release Pillow / style memory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

ALLOWED_COVER_STYLES = frozenset(
    {"style_multi_1", "style_shelf_1", "style_single_1", "style_single_2"}
)


def _validate_job(job: Dict[str, Any]) -> None:
    style = job.get("style_name")
    if style not in ALLOWED_COVER_STYLES:
        raise ValueError(f"invalid style_name: {style!r}")
    for key in ("title_zh", "title_en", "zh_font_path", "en_font_path", "library_dir", "output_path"):
        if key not in job:
            raise ValueError(f"missing job key: {key}")


async def run_cover_worker_job(job: Dict[str, Any], *, timeout_sec: float = 300.0) -> None:
    _validate_job(job)
    fd, raw_path = tempfile.mkstemp(suffix=".json", prefix="cover_job_")
    os.close(fd)
    job_path = Path(raw_path)
    try:
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "cover_worker",
            str(job_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("cover worker timed out") from None
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace").strip()
            out = (stdout or b"").decode("utf-8", errors="replace").strip()
            msg = err or out or f"exit code {proc.returncode}"
            logger.error("cover_worker failed: %s", msg)
            raise RuntimeError(f"cover worker failed: {msg}")
    finally:
        try:
            job_path.unlink(missing_ok=True)
        except OSError:
            pass
