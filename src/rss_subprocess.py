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
        # 继承父进程 stdout/stderr，子进程内 rss_processor 的 info 日志才会出现在容器/终端日志里；
        # 若用 PIPE 且成功路径不转发，豆瓣/TMDB 等日志会被吞掉。
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "rss_worker",
            str(job_path),
            env=os.environ.copy(),
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("rss worker timed out") from None
        if proc.returncode != 0:
            logger.error("rss_worker exited with code %s (see logs above from child process)", proc.returncode)
            raise RuntimeError(f"rss worker failed with exit code {proc.returncode}")
    finally:
        try:
            job_path.unlink(missing_ok=True)
        except OSError:
            pass
