# src/proxy_cache.py
"""
Virtual library cache layer.

Per-server per-user per-virtual-library SQLite files under:
config/vlib/servers/{server_id}/users/{user_id}/{vlib_id}/items.db.
No long-lived connections: open → read/write → close to avoid memory growth from many handles.

Legacy config/vlib_cache.db (single table) is no longer written; old rows are ignored.
"""

from __future__ import annotations

import json
import re
import logging
import shutil
import sqlite3
import threading
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from cachetools import TTLCache
from starlette.requests import Request

logger = logging.getLogger(__name__)

api_cache = TTLCache(maxsize=500, ttl=300)


def get_api_cache_key(request: Request, full_path: str) -> Optional[str]:
    """
    用于代理内存缓存的键：仅 GET、排除图片等二进制路径。
    与 UserId + path + 查询参数（脱敏 token）相关。
    """
    if request.method != "GET":
        return None
    if "/Images/" in full_path or full_path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return None
    if request.headers.get("Range"):
        return None
    params = dict(request.query_params)
    params.pop("X-Emby-Token", None)
    params.pop("api_key", None)
    sorted_params = tuple(sorted(params.items()))
    user_id_from_path = "public"
    if "/Users/" in full_path:
        try:
            parts = full_path.split("/")
            user_id_from_path = parts[parts.index("Users") + 1]
        except (ValueError, IndexError):
            pass
    user_id = params.get("UserId", user_id_from_path)
    return f"user:{user_id}:path:{full_path}:params:{sorted_params}"

# ---------------------------------------------------------------------------
# Field slimming
# ---------------------------------------------------------------------------

KEEP_FIELDS = frozenset({
    "Id", "Name", "SortName", "Type", "ServerId",
    "ImageTags", "BackdropImageTags",
    "ProductionYear", "CommunityRating", "OfficialRating",
    "RunTimeTicks", "UserData", "SeriesName",
    "IsFolder", "CollectionType",
    "ProviderIds", "Genres", "Tags", "Studios",
    "DateCreated", "DateLastMediaAdded", "PremiereDate",
    "Container", "VideoRange", "ProductionLocations",
    "CriticRating", "SeriesStatus",
    "ParentId", "SeriesId", "SeasonId", "SeasonName",
})


def slim_item(item: dict) -> dict:
    return {k: item[k] for k in KEEP_FIELDS if k in item}


def slim_items(items: list[dict]) -> list[dict]:
    return [slim_item(it) for it in items]


# ---------------------------------------------------------------------------
# Per-user per-vlib SQLite (filesystem layout)
# ---------------------------------------------------------------------------

# config/ is sibling to src/ at runtime image layout /app/config
VLIB_CACHE_ROOT = Path(__file__).resolve().parent.parent / "config" / "vlib" / "servers"

_write_lock = threading.Lock()


def _safe_fs_segment(part: str, name: str) -> str:
    """Map Emby ids to a single path segment (no slashes / traversal)."""
    if not part or not str(part).strip():
        raise ValueError(f"empty {name}")
    s = re.sub(r"[^a-zA-Z0-9\-]", "_", str(part).strip())
    if not s or s.replace("_", "") == "":
        raise ValueError(f"invalid {name} for vlib cache path")
    return s


def _items_db_path(server_id: str, user_id: str, vlib_id: str) -> Path:
    s = _safe_fs_segment(server_id, "server_id")
    u = _safe_fs_segment(user_id, "user_id")
    v = _safe_fs_segment(vlib_id, "vlib_id")
    return VLIB_CACHE_ROOT / s / "users" / u / v / "items.db"


def list_user_ids_with_vlib_cache(server_id: str, vlib_id: str) -> list[str]:
    """
    Emby user ids that already have items.db for this virtual library
    (i.e. have browsed this vlib at least once).
    """
    try:
        s_seg = _safe_fs_segment(server_id, "server_id")
        v_seg = _safe_fs_segment(vlib_id, "vlib_id")
    except ValueError:
        return []
    root = VLIB_CACHE_ROOT / s_seg / "users"
    if not root.is_dir():
        return []
    out: list[str] = []
    for user_dir in sorted(root.iterdir()):
        if not user_dir.is_dir():
            continue
        if (user_dir / v_seg / "items.db").is_file():
            out.append(user_dir.name)
    return out


def _init_items_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items_payload (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            items_z BLOB NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)


class VLibUserItemsCache:
    """
    One compressed JSON blob per (user_id, vlib_id), stored on disk.
    Reads/writes do not keep connections open.
    """

    def get_for_user(
        self,
        server_id: str,
        user_id: str,
        vlib_id: str,
        *,
        max_age_seconds: Optional[float] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        path = _items_db_path(server_id, user_id, vlib_id)
        if not path.is_file():
            return None
        try:
            conn = sqlite3.connect(str(path), timeout=60.0, check_same_thread=False)
            try:
                _init_items_db(conn)
                row = conn.execute(
                    "SELECT items_z, updated_at FROM items_payload WHERE id = 1"
                ).fetchone()
                if not row or row[0] is None:
                    return None
                items_z, updated_at_str = row[0], row[1]
                if max_age_seconds is not None and max_age_seconds > 0:
                    if not updated_at_str:
                        return None
                    try:
                        s = str(updated_at_str).replace("Z", "+00:00")
                        u = datetime.fromisoformat(s)
                        if u.tzinfo is None:
                            u = u.replace(tzinfo=timezone.utc)
                        else:
                            u = u.astimezone(timezone.utc)
                        age = (datetime.now(timezone.utc) - u).total_seconds()
                        if age > max_age_seconds:
                            logger.debug(
                                "VLibUserItemsCache stale user=%s vlib=%s age=%.0fs max=%.0fs",
                                user_id,
                                vlib_id,
                                age,
                                max_age_seconds,
                            )
                            return None
                    except Exception as ex:
                        logger.warning(
                            "VLibUserItemsCache bad updated_at user=%s vlib=%s: %s",
                            user_id,
                            vlib_id,
                            ex,
                        )
                        return None
                return json.loads(zlib.decompress(items_z))
            finally:
                conn.close()
        except Exception as e:
            logger.warning(
                "VLibUserItemsCache.get_for_user(%s, %s, %s) failed: %s",
                server_id,
                user_id,
                vlib_id,
                e,
            )
            return None

    def set_for_user(self, server_id: str, user_id: str, vlib_id: str, items: List[Dict[str, Any]]) -> None:
        path = _items_db_path(server_id, user_id, vlib_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(items, ensure_ascii=False).encode("utf-8")
        compressed = zlib.compress(raw, level=6)
        updated = datetime.utcnow().isoformat()
        with _write_lock:
            conn = sqlite3.connect(str(path), timeout=60.0, check_same_thread=False)
            try:
                _init_items_db(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO items_payload (id, items_z, updated_at) VALUES (1, ?, ?)",
                    (compressed, updated),
                )
                conn.commit()
            finally:
                conn.close()

    def has_for_user(self, server_id: str, user_id: str, vlib_id: str) -> bool:
        path = _items_db_path(server_id, user_id, vlib_id)
        return path.is_file()

    def delete_for_user(self, server_id: str, user_id: str, vlib_id: str) -> bool:
        path = _items_db_path(server_id, user_id, vlib_id)
        parent = path.parent
        try:
            if parent.is_dir():
                shutil.rmtree(parent, ignore_errors=True)
                return True
        except Exception as e:
            logger.warning("delete_for_user failed %s/%s/%s: %s", server_id, user_id, vlib_id, e)
        return False

    def delete_vlib_all_users(self, server_id: str, vlib_id: str) -> int:
        """Remove this vlib's cache directory under every user. Returns user dirs removed."""
        s_seg = _safe_fs_segment(server_id, "server_id")
        v_seg = _safe_fs_segment(vlib_id, "vlib_id")
        removed = 0
        root = VLIB_CACHE_ROOT / s_seg / "users"
        if not root.is_dir():
            return 0
        with _write_lock:
            for user_dir in root.iterdir():
                if not user_dir.is_dir():
                    continue
                target = user_dir / v_seg
                if target.is_dir():
                    try:
                        shutil.rmtree(target, ignore_errors=True)
                        removed += 1
                    except Exception as e:
                        logger.warning("delete_vlib_all_users: %s: %s", target, e)
        return removed


vlib_items_cache = VLibUserItemsCache()


def clear_vlib_items_cache(server_id: str, vlib_id: str) -> dict:
    """
    Drop on-disk cache for this virtual library for all users.
    Returns user_ids that had cache *before* deletion (for targeted refresh).
    """
    user_ids = list_user_ids_with_vlib_cache(server_id, vlib_id)
    n = vlib_items_cache.delete_vlib_all_users(server_id, vlib_id)
    return {
        "main": n,
        "random_scoped": 0,
        "user_dirs_removed": n,
        "user_ids": user_ids,
    }


def clear_vlib_page_cache(vlib_id: str):
    logger.debug(f"clear_vlib_page_cache({vlib_id}) called (no-op, page cache removed)")
