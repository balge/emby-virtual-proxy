# src/proxy_cache.py
"""
Virtual library cache layer.

Design:
- Full item lists are cached in memory (and persisted to SQLite for restart recovery).
- All browsing requests are served by slicing from the in-memory full list.
- Data is populated on startup / manual refresh / webhook / scheduled refresh.
- Items are "slimmed" before caching to keep memory usage low (~300-600 bytes per item
  instead of 3-8 KB for a full Emby response).
"""

import json
import sqlite3
import threading
import logging
from datetime import datetime, timedelta
from pathlib import Path
from cachetools import TTLCache

logger = logging.getLogger(__name__)

# API response cache: short-lived, no persistence needed
# Default TTL: 2 hours (used by admin_server for general API caching)
api_cache = TTLCache(maxsize=500, ttl=7200)

# ---------------------------------------------------------------------------
# Field slimming
# ---------------------------------------------------------------------------

# Fields needed for poster-wall display + filtering + sorting.
# Everything else (Overview, People, MediaSources, Chapters, etc.) is dropped.
KEEP_FIELDS = frozenset({
    # Display
    "Id", "Name", "SortName", "Type", "ServerId",
    "ImageTags", "BackdropImageTags",
    "ProductionYear", "CommunityRating", "OfficialRating",
    "RunTimeTicks", "UserData", "SeriesName",
    "IsFolder", "CollectionType",
    # Filter & sort
    "ProviderIds", "Genres", "Tags", "Studios",
    "DateCreated", "DateLastMediaAdded", "PremiereDate",
    "Container", "VideoRange", "ProductionLocations",
    "CriticRating", "SeriesStatus",
    # Needed by some clients / handlers
    "ParentId", "SeriesId", "SeasonId", "SeasonName",
})


def slim_item(item: dict) -> dict:
    """Strip an Emby item dict down to only the fields we need."""
    return {k: item[k] for k in KEEP_FIELDS if k in item}


def slim_items(items: list[dict]) -> list[dict]:
    """Slim a list of items."""
    return [slim_item(it) for it in items]


# ---------------------------------------------------------------------------
# Persistent cache backed by SQLite
# ---------------------------------------------------------------------------

_DB_PATH = Path(__file__).parent.parent / "config" / "vlib_cache.db"
_db_lock = threading.Lock()
_mem_lock = threading.Lock()


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
    conn.execute("DROP TABLE IF EXISTS random_recommend")
    conn.commit()
    conn.close()


_init_db()


class VLibItemsCache:
    """
    In-memory full-item-list cache backed by SQLite for persistence across restarts.

    Keys:
    - "<vlib_id>"                  → full item list for a virtual library
    - "random:<user_id>:<vlib_id>" → random recommendations per user
    """

    def __init__(self):
        self._mem: dict[str, list[dict]] = {}
        self._conn: sqlite3.Connection | None = None
        self._load_from_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = _get_db()
        return self._conn

    def _load_from_db(self):
        try:
            conn = self._get_conn()
            rows = conn.execute("SELECT vlib_id, items_json FROM vlib_items").fetchall()
            for vlib_id, items_json in rows:
                try:
                    self._mem[vlib_id] = json.loads(items_json)
                except json.JSONDecodeError:
                    pass
            logger.info(f"Loaded {len(self._mem)} vlib item caches from DB.")
        except Exception as e:
            logger.warning(f"Failed to load vlib_items from DB: {e}")

    # -- read --

    def get(self, key: str, default=None) -> list[dict] | None:
        with _mem_lock:
            return self._mem.get(key, default)

    def __contains__(self, key: str) -> bool:
        with _mem_lock:
            return key in self._mem

    def __getitem__(self, key: str) -> list[dict]:
        with _mem_lock:
            return self._mem[key]

    # -- write --

    def set(self, key: str, items: list[dict], *, persist: bool = True):
        """Store items (already slimmed) into cache."""
        with _mem_lock:
            self._mem[key] = items
        if persist:
            self._persist(key, items)

    def __setitem__(self, key: str, value: list[dict]):
        self.set(key, value)

    def pop(self, key: str, *args):
        with _mem_lock:
            val = self._mem.pop(key, *args)
        self._delete_from_db(key)
        return val

    def keys(self) -> list[str]:
        with _mem_lock:
            return list(self._mem.keys())

    # -- persistence --

    def _persist(self, key: str, value: list[dict]):
        try:
            with _db_lock:
                conn = self._get_conn()
                conn.execute(
                    "INSERT OR REPLACE INTO vlib_items (vlib_id, items_json, updated_at) VALUES (?, ?, ?)",
                    (key, json.dumps(value, ensure_ascii=False), datetime.utcnow().isoformat()),
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to persist vlib_items for {key}: {e}")

    def _delete_from_db(self, key: str):
        try:
            with _db_lock:
                conn = self._get_conn()
                conn.execute("DELETE FROM vlib_items WHERE vlib_id = ?", (key,))
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to delete vlib_items for {key}: {e}")


# ---------------------------------------------------------------------------
# Module-level cache instance
# ---------------------------------------------------------------------------
vlib_items_cache = VLibItemsCache()


# ---------------------------------------------------------------------------
# Cache invalidation helpers
# ---------------------------------------------------------------------------

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


def clear_vlib_page_cache(vlib_id: str):
    """No-op kept for backward compatibility. Page cache has been removed."""
    logger.debug(f"clear_vlib_page_cache({vlib_id}) called (no-op, page cache removed)")
