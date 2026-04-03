# src/proxy_cache.py
"""
Virtual library cache layer.

Design:
- Full item lists are persisted in SQLite as gzip-compressed BLOBs and read on-demand.
- This avoids ~500 MB RAM overhead and reduces DB size by ~60-70% vs plain JSON TEXT.
- Browsing requests: decompress + json.loads per-request (~20-80 ms, IO savings offset
  decompression cost).
- Items are "slimmed" before caching to keep per-item size low (~300-600 bytes).
"""

import json
import sqlite3
import threading
import logging
import zlib
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
            items_json TEXT,
            items_data BLOB,
            updated_at TEXT NOT NULL
        )
    """)
    # Migrate: add items_data column if missing (upgrade from TEXT-only schema)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(vlib_items)").fetchall()}
    if "items_data" not in cols:
        conn.execute("ALTER TABLE vlib_items ADD COLUMN items_data BLOB")
        conn.commit()
        logger.info("Added items_data BLOB column to vlib_items table.")
    # Migrate old TEXT rows → compressed BLOB, then clear TEXT column
    rows = conn.execute(
        "SELECT vlib_id, items_json FROM vlib_items WHERE items_data IS NULL AND items_json IS NOT NULL"
    ).fetchall()
    if rows:
        for vlib_id, items_json in rows:
            compressed = zlib.compress(items_json.encode("utf-8"), level=6)
            conn.execute(
                "UPDATE vlib_items SET items_data = ?, items_json = NULL WHERE vlib_id = ?",
                (compressed, vlib_id),
            )
        conn.commit()
        logger.info(f"Migrated {len(rows)} vlib_items rows from TEXT to compressed BLOB.")
    conn.execute("DROP TABLE IF EXISTS random_recommend")
    conn.commit()
    conn.close()


_init_db()


class VLibItemsCache:
    """
    DB-only virtual library item cache.

    All reads go directly to SQLite (WAL mode) — no in-memory copy.
    This trades ~20-80 ms per read for massive memory savings
    (40 MB DB was inflating to ~500 MB in RAM after json.loads).
    """

    def __init__(self):
        self._conn: sqlite3.Connection | None = None
        logger.info("VLibItemsCache initialized (DB-only mode, no in-memory loading).")

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = _get_db()
        return self._conn

    # -- read --

    def get(self, key: str, default=None) -> list[dict] | None:
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT items_data, items_json FROM vlib_items WHERE vlib_id = ?", (key,)
            ).fetchone()
            if row is None:
                return default
            items_data, items_json = row
            if items_data is not None:
                return json.loads(zlib.decompress(items_data))
            if items_json is not None:
                return json.loads(items_json)
            return default
        except Exception as e:
            logger.warning(f"VLibItemsCache.get({key}) failed: {e}")
            return default

    def __contains__(self, key: str) -> bool:
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT 1 FROM vlib_items WHERE vlib_id = ? LIMIT 1", (key,)
            ).fetchone()
            return row is not None
        except Exception as e:
            logger.warning(f"VLibItemsCache.__contains__({key}) failed: {e}")
            return False

    def __getitem__(self, key: str) -> list[dict]:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    # -- write --

    def set(self, key: str, items: list[dict], *, persist: bool = True):
        if persist:
            self._persist(key, items)

    def __setitem__(self, key: str, value: list[dict]):
        self._persist(key, value)

    def pop(self, key: str, *args):
        val = self.get(key)
        self._delete_from_db(key)
        if val is None and args:
            return args[0]
        return val

    def keys(self) -> list[str]:
        try:
            conn = self._get_conn()
            rows = conn.execute("SELECT vlib_id FROM vlib_items").fetchall()
            return [r[0] for r in rows]
        except Exception as e:
            logger.warning(f"VLibItemsCache.keys() failed: {e}")
            return []

    def delete_by_prefix_suffix(self, prefix: str, suffix: str) -> int:
        """Delete rows where vlib_id starts with prefix AND ends with suffix. Returns count."""
        try:
            with _db_lock:
                conn = self._get_conn()
                cursor = conn.execute(
                    "DELETE FROM vlib_items WHERE vlib_id LIKE ? AND vlib_id LIKE ?",
                    (f"{prefix}%", f"%{suffix}"),
                )
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            logger.warning(f"VLibItemsCache.delete_by_prefix_suffix failed: {e}")
            return 0

    # -- persistence --

    def _persist(self, key: str, value: list[dict]):
        try:
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
            compressed = zlib.compress(raw, level=6)
            with _db_lock:
                conn = self._get_conn()
                conn.execute(
                    "INSERT OR REPLACE INTO vlib_items (vlib_id, items_data, items_json, updated_at) VALUES (?, ?, NULL, ?)",
                    (key, compressed, datetime.utcnow().isoformat()),
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

    # Use SQL LIKE to efficiently find and delete random:*:<vlib_id> keys
    removed_random = vlib_items_cache.delete_by_prefix_suffix("random:", f":{vlib_id}")

    return {"main": removed_main, "random_scoped": removed_random}


def clear_vlib_page_cache(vlib_id: str):
    """No-op kept for backward compatibility. Page cache has been removed."""
    logger.debug(f"clear_vlib_page_cache({vlib_id}) called (no-op, page cache removed)")
