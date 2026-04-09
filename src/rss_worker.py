"""
Short-lived subprocess: RSS pull + parse + TMDB match + DB writes.

Run: python -m rss_worker <path-to-job.json>
Job JSON: {"rss_type": "douban"|"bangumi", "vlib": { ... VirtualLibrary fields ... }}
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from pathlib import Path

_LOG_LEVEL_NAME = __import__("os").environ.get("LOG_LEVEL", "info").upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_NAME, logging.INFO)
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("rss_worker")


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m rss_worker <job.json>", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1])
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"invalid job file: {e}", file=sys.stderr)
        sys.exit(2)

    rss_type = job.get("rss_type")
    vlib_data = job.get("vlib")
    if not rss_type or vlib_data is None:
        print("job must contain rss_type and vlib", file=sys.stderr)
        sys.exit(2)

    from models import VirtualLibrary

    vlib = VirtualLibrary.model_validate(vlib_data)

    try:
        if rss_type == "douban":
            from rss_processor.douban import DoubanProcessor

            DoubanProcessor(vlib).process()
        elif rss_type == "bangumi":
            from rss_processor.bangumi import BangumiProcessor

            BangumiProcessor(vlib).process()
        else:
            print(f"unknown rss_type: {rss_type}", file=sys.stderr)
            sys.exit(2)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
