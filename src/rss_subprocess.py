"""
Run RSS fetch/parse/TMDB matching in a short-lived subprocess so BeautifulSoup/requests
heavy work does not stay in the main admin+proxy process after each refresh.
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

from models import VirtualLibrary

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = 3600.0


async def run_rss_refresh_job(vlib: VirtualLibrary, *, timeout_sec: float = DEFAULT_TIMEOUT_SEC) -> None:
    if vlib.rss_type not in ("douban", "bangumi"):
        raise ValueError(f"unsupported rss_type for rss_worker: {vlib.rss_type!r}")

    job: Dict[str, Any] = {
        "rss_type": vlib.rss_type,
        "vlib": vlib.model_dump(mode="json"),
    }
    fd, raw_path = tempfile.mkstemp(suffix=".json", prefix="rss_job_")
    os.close(fd)
    job_path = Path(raw_path)
    try:
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "rss_worker",
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
            raise RuntimeError("rss worker timed out") from None
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace").strip()
            out = (stdout or b"").decode("utf-8", errors="replace").strip()
            msg = err or out or f"exit code {proc.returncode}"
            logger.error("rss_worker failed: %s", msg)
            raise RuntimeError(f"rss worker failed: {msg}")
    finally:
        try:
            job_path.unlink(missing_ok=True)
        except OSError:
            pass
