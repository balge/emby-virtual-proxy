# src/proxy_cache.py

import json
import sqlite3
import threading
import logging
from datetime import datetime, timedelta
from pathlib import Path
from cachetools import TTLCache

logger = logging.getLogger(__name__)

# API response cache: short-lived, no persistence needed
# Default TTL: 2 hours (helps heavy virtual library browsing)
api_cache = TTLCache(maxsize=500, ttl=7200)

# --- Persistent cache backed by SQLite ---

_DB_PATH = Path(__file__).parent.parent / "config" / "vlib_cache.db"
_db_lock = threading.Lock()


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    _DB_PATH.parent.mkdir(exist_ok=True)
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vlib_items (
            vlib_id TEXT PRIMARY KEY,
            items_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    # random_recommend: deprecated (random now unified into vlib_items_cache)
    conn.execute("DROP TABLE IF EXISTS random_recommend")
    conn.commit()
    conn.close()

_init_db()

class PersistentCache:
    """In-memory dict backed by SQLite for vlib_items (no TTL)."""

    def __init__(self):
        self._mem = {}
        self._load_from_db()

    def _load_from_db(self):
        try:
            conn = _get_db()
            rows = conn.execute("SELECT vlib_id, items_json FROM vlib_items").fetchall()
            conn.close()
            for vlib_id, items_json in rows:
                try:
                    self._mem[vlib_id] = json.loads(items_json)
                except json.JSONDecodeError:
                    pass
            logger.info(f"Loaded {len(self._mem)} vlib item caches from DB.")
        except Exception as e:
            logger.warning(f"Failed to load vlib_items from DB: {e}")

    def get(self, key, default=None):
        return self._mem.get(key, default)

    def __contains__(self, key):
        return key in self._mem

    def __setitem__(self, key, value):
        self._mem[key] = value
        self._persist(key, value)

    def __getitem__(self, key):
        return self._mem[key]

    def pop(self, key, *args):
        val = self._mem.pop(key, *args)
        self._delete_from_db(key)
        return val

    def keys(self):
        return list(self._mem.keys())

    def _persist(self, key, value):
        try:
            with _db_lock:
                conn = _get_db()
                conn.execute(
                    "INSERT OR REPLACE INTO vlib_items (vlib_id, items_json, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(value, ensure_ascii=False), datetime.utcnow().isoformat())
                )
                conn.commit()
                conn.close()
        except Exception as e:
            logger.warning(f"Failed to persist vlib_items for {key}: {e}")

    def _delete_from_db(self, key):
        try:
            with _db_lock:
                conn = _get_db()
                conn.execute("DELETE FROM vlib_items WHERE vlib_id = ?", (key,))
                conn.commit()
                conn.close()
        except Exception as e:
            logger.warning(f"Failed to delete vlib_items for {key}: {e}")


# --- Module-level cache instances ---
vlib_items_cache = PersistentCache()

# Virtual-library paged response cache (in-memory, invalidated by refresh/webhook operations).
_vlib_page_cache: dict[tuple[str, str, int, int], dict] = {}
_vlib_page_cache_lock = threading.Lock()
_vlib_page_access_seq = 0
# Per (vlib, context, limit): keep page1/page2 pinned + up to this many extra pages (LRU)
VLIB_PAGE_CACHE_EXTRA_PAGES = 4


def get_vlib_page_cache(vlib_id: str, context_key: str, start_index: int, limit: int):
    key = (vlib_id, context_key, int(start_index), int(limit))
    with _vlib_page_cache_lock:
        entry = _vlib_page_cache.get(key)
        if entry is None:
            return None
        # Backward compatible with old shape: raw dict payload
        if "data" not in entry:
            return entry
        expires_at = entry.get("expires_at")
        if expires_at and datetime.utcnow().isoformat() > expires_at:
            _vlib_page_cache.pop(key, None)
            return None
        global _vlib_page_access_seq
        _vlib_page_access_seq += 1
        entry["last_access"] = _vlib_page_access_seq
        return entry.get("data")


def _prune_vlib_page_group(vlib_id: str, context_key: str, limit: int):
    """
    Fine-grained policy:
    - Always keep page1(start=0) and page2(start=limit)
    - Remaining pages keep most-recent N (LRU)
    """
    group_keys = [
        k for k in _vlib_page_cache
        if k[0] == vlib_id and k[1] == context_key and k[3] == int(limit)
    ]
    if not group_keys:
        return

    pinned_starts = {0, int(limit)}
    pinned = []
    candidates = []
    for k in group_keys:
        entry = _vlib_page_cache.get(k) or {}
        start = k[2]
        if start in pinned_starts:
            pinned.append(k)
            continue
        if "data" in entry:
            candidates.append((entry.get("last_access", 0), k))
        else:
            # old shape, treat as high-priority once
            candidates.append((10**18, k))

    # Keep most recently accessed extra pages
    candidates.sort(key=lambda x: x[0], reverse=True)
    keep_extra = {k for _, k in candidates[:VLIB_PAGE_CACHE_EXTRA_PAGES]}
    keep = set(pinned) | keep_extra
    for k in group_keys:
        if k not in keep:
            _vlib_page_cache.pop(k, None)


def set_vlib_page_cache(vlib_id: str, context_key: str, start_index: int, limit: int, data: dict, ttl_seconds: int | None = None):
    key = (vlib_id, context_key, int(start_index), int(limit))
    with _vlib_page_cache_lock:
        global _vlib_page_access_seq
        _vlib_page_access_seq += 1
        expires_at = None
        if ttl_seconds and ttl_seconds > 0:
            expires_at = (datetime.utcnow() + timedelta(seconds=int(ttl_seconds))).isoformat()
        _vlib_page_cache[key] = {
            "data": data,
            "last_access": _vlib_page_access_seq,
            "expires_at": expires_at,
        }
        logger.debug(
            f"VLIB_PAGE_CACHE SET vlib={vlib_id} start={start_index} limit={limit} "
            f"ttl={ttl_seconds if ttl_seconds is not None else 'none'}"
        )
        _prune_vlib_page_group(vlib_id, context_key, int(limit))


def clear_vlib_page_cache(vlib_id: str):
    with _vlib_page_cache_lock:
        keys = [k for k in _vlib_page_cache if k[0] == vlib_id]
        for k in keys:
            _vlib_page_cache.pop(k, None)
        logger.info(f"VLIB_PAGE_CACHE CLEAR vlib={vlib_id} removed_pages={len(keys)}")


def clear_vlib_items_cache(vlib_id: str) -> dict:
    """
    Clear all vlib_items_cache entries related to this virtual library:
    - Main key: <vlib_id>
    - Random per-user keys: random:<user_id>:<vlib_id>
    """
    removed_main = 0
    removed_random = 0

    if vlib_id in vlib_items_cache:
        vlib_items_cache.pop(vlib_id, None)
        removed_main = 1

    suffix = f":{vlib_id}"
    for k in vlib_items_cache.keys():
        if isinstance(k, str) and k.startswith("random:") and k.endswith(suffix):
            vlib_items_cache.pop(k, None)
            removed_random += 1

    return {"main": removed_main, "random_scoped": removed_random}
