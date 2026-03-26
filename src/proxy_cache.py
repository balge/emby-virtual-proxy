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
api_cache = TTLCache(maxsize=500, ttl=300)

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS random_recommend (
            cache_key TEXT PRIMARY KEY,
            items_json TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
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


class PersistentTTLCache:
    """In-memory dict backed by SQLite with TTL expiration."""

    def __init__(self, ttl_seconds: int = 43200):
        self._mem = {}
        self._ttl = ttl_seconds
        self._load_from_db()

    def _load_from_db(self):
        try:
            now = datetime.utcnow().isoformat()
            conn = _get_db()
            # Only load non-expired entries
            rows = conn.execute(
                "SELECT cache_key, items_json, expires_at FROM random_recommend WHERE expires_at > ?",
                (now,)
            ).fetchall()
            # Clean up expired
            conn.execute("DELETE FROM random_recommend WHERE expires_at <= ?", (now,))
            conn.commit()
            conn.close()
            for cache_key, items_json, expires_at in rows:
                try:
                    self._mem[cache_key] = {
                        "data": json.loads(items_json),
                        "expires_at": expires_at
                    }
                except json.JSONDecodeError:
                    pass
            logger.info(f"Loaded {len(self._mem)} random recommend caches from DB.")
        except Exception as e:
            logger.warning(f"Failed to load random_recommend from DB: {e}")

    def get(self, key, default=None):
        entry = self._mem.get(key)
        if entry is None:
            return default
        if datetime.utcnow().isoformat() > entry["expires_at"]:
            self._mem.pop(key, None)
            self._delete_from_db(key)
            return default
        return entry["data"]

    def __contains__(self, key):
        return self.get(key) is not None

    def __setitem__(self, key, value):
        expires_at = (datetime.utcnow() + timedelta(seconds=self._ttl)).isoformat()
        self._mem[key] = {"data": value, "expires_at": expires_at}
        self._persist(key, value, expires_at)

    def __getitem__(self, key):
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def pop(self, key, *args):
        entry = self._mem.pop(key, None)
        self._delete_from_db(key)
        if entry is None:
            if args:
                return args[0]
            raise KeyError(key)
        return entry["data"]

    def __iter__(self):
        """Iterate over non-expired keys."""
        now = datetime.utcnow().isoformat()
        return iter([k for k, v in self._mem.items() if v["expires_at"] > now])

    def _persist(self, key, value, expires_at):
        try:
            with _db_lock:
                conn = _get_db()
                conn.execute(
                    "INSERT OR REPLACE INTO random_recommend (cache_key, items_json, expires_at) VALUES (?, ?, ?)",
                    (key, json.dumps(value, ensure_ascii=False), expires_at)
                )
                conn.commit()
                conn.close()
        except Exception as e:
            logger.warning(f"Failed to persist random_recommend for {key}: {e}")

    def _delete_from_db(self, key):
        try:
            with _db_lock:
                conn = _get_db()
                conn.execute("DELETE FROM random_recommend WHERE cache_key = ?", (key,))
                conn.commit()
                conn.close()
        except Exception as e:
            logger.warning(f"Failed to delete random_recommend for {key}: {e}")


# --- Module-level cache instances ---
vlib_items_cache = PersistentCache()
random_recommend_cache = PersistentTTLCache(ttl_seconds=43200)  # 12 hours
