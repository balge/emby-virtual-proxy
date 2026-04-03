# src/vlib_cache_manager.py
"""
Virtual library cache manager.

Single responsibility: fetch full item lists from Emby, slim them,
and push into proxy process's vlib_items_cache via internal HTTP API.

Runs in the admin process. Does NOT import or hold vlib_items_cache in memory.

Triggered by:
- Startup (populate all non-RSS/non-random libraries)
- Manual refresh (admin UI "刷新数据" button)
- Webhook (Emby library.changed events)
- Scheduled refresh (APScheduler cron)
"""

import logging
import os
import json
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import aiohttp
from models import AppConfig, VirtualLibrary
from proxy_cache import slim_items
from proxy_handlers._filter_translator import translate_rules
from proxy_handlers.handler_merger import merge_items_by_tmdb

logger = logging.getLogger(__name__)

# Base URL for proxy internal API
_PROXY_BASE = os.environ.get("PROXY_CORE_URL", "http://localhost:8999").rstrip("/")


def _internal_headers() -> Dict[str, str]:
    token = os.environ.get("INTERNAL_CACHE_TOKEN", "").strip()
    return {"X-Internal-Token": token} if token else {}


async def _push_cache_to_proxy(
    session: aiohttp.ClientSession, vlib_id: str, items: List[Dict]
) -> bool:
    """Write slimmed items to proxy process via internal API."""
    url = f"{_PROXY_BASE}/api/internal/set-cached-items/{vlib_id}"
    try:
        async with session.post(
            url, json={"Items": items}, headers=_internal_headers(),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 200:
                logger.info(f"Pushed {len(items)} items to proxy cache for '{vlib_id}'")
                return True
            text = await resp.text()
            logger.warning(f"Push cache failed for '{vlib_id}': HTTP {resp.status} {text[:200]}")
            return False
    except Exception as e:
        logger.warning(f"Push cache failed for '{vlib_id}': {e}")
        return False


async def _cache_exists_in_proxy(
    session: aiohttp.ClientSession, vlib_id: str
) -> bool:
    """Check if proxy already has cache for this vlib."""
    url = f"{_PROXY_BASE}/api/internal/cache-exists/{vlib_id}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("exists", False)
    except Exception:
        pass
    return False

# ---------------------------------------------------------------------------
# Fields to request from Emby (superset for poster wall + filter + sort)
# ---------------------------------------------------------------------------
FETCH_FIELDS = ",".join([
    "ProviderIds", "Genres", "Tags", "Studios", "OfficialRatings",
    "CommunityRating", "ProductionYear", "VideoRange", "Container",
    "ProductionLocations", "DateLastMediaAdded", "DateCreated",
    "ImageTags", "BackdropImageTags", "SortName", "PremiereDate",
    "CriticRating", "SeriesStatus", "RunTimeTicks",
])


# ---------------------------------------------------------------------------
# Low-level Emby fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_all_pages(
    session: aiohttp.ClientSession,
    url: str,
    params: Dict[str, Any],
    headers: Dict[str, str],
    page_size: int = 400,
) -> List[Dict]:
    """Paginate through Emby Items endpoint and return all items."""
    all_items: List[Dict] = []
    start = 0
    while True:
        p = dict(params)
        p["StartIndex"] = str(start)
        p["Limit"] = str(page_size)
        try:
            async with session.get(url, params=p, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    logger.error(f"Emby fetch failed: status={resp.status}, url={url}")
                    break
                data = await resp.json()
                batch = data.get("Items", [])
                if not batch:
                    break
                all_items.extend(batch)
                start += len(batch)
                if len(batch) < page_size:
                    break
        except Exception as e:
            logger.error(f"Emby fetch error: {e}")
            break
    return all_items


def _deduplicate(items: List[Dict]) -> List[Dict]:
    seen: set = set()
    out: List[Dict] = []
    for item in items:
        iid = item.get("Id")
        if iid:
            if iid in seen:
                continue
            seen.add(iid)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Post-filter helpers (reused from handler_items via import)
# ---------------------------------------------------------------------------

def _get_nested_value(item: Dict[str, Any], field_path: str) -> Any:
    keys = field_path.split('.')
    value = item
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return None
    return value


def _get_value_for_rule(item: Dict[str, Any], field: str) -> Any:
    if field == "DateLastMediaAdded":
        if item.get("Type") == "Series":
            val = _get_nested_value(item, "DateLastMediaAdded")
            return val if val else _get_nested_value(item, "DateCreated")
        return _get_nested_value(item, "DateCreated")
    return _get_nested_value(item, field)


def _apply_custom_sort(items: List[Dict], sort_field: str, sort_order: str) -> List[Dict]:
    if not items or not sort_field or not sort_order:
        return items
    reverse = (sort_order == "desc")

    def sort_key(item):
        val = _get_value_for_rule(item, sort_field)
        if val is None:
            return (1, "")
        try:
            return (0, float(val))
        except (ValueError, TypeError):
            return (0, str(val))

    items.sort(key=sort_key, reverse=reverse)
    return items


# ---------------------------------------------------------------------------
# DLA (DateLastMediaAdded) optimized fetch
# ---------------------------------------------------------------------------

def _parse_iso_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        s = str(value)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


async def _fetch_recent_episodes_series_ids(
    session: aiohttp.ClientSession, url: str, headers: Dict,
    base_params: Dict, threshold_dt: datetime,
    source_parent_ids: Optional[List[str]],
) -> tuple[set[str], Dict[str, datetime]]:
    """Scan episodes by DateCreated desc, collect Series IDs newer than threshold."""
    page_limit = 400

    async def _scan(parent_id: Optional[str]):
        ids: set[str] = set()
        latest: Dict[str, datetime] = {}
        start = 0
        while True:
            p = dict(base_params)
            p.update({"IncludeItemTypes": "Episode", "Recursive": "true",
                       "SortBy": "DateCreated", "SortOrder": "Descending",
                       "Fields": "DateCreated,SeriesId",
                       "StartIndex": str(start), "Limit": str(page_limit)})
            if parent_id:
                p["ParentId"] = parent_id
            try:
                async with session.get(url, params=p, headers=headers) as resp:
                    if resp.status != 200:
                        return ids, latest
                    data = await resp.json()
                    items = data.get("Items", []) if isinstance(data, dict) else []
                    if not items:
                        return ids, latest
                    oldest = None
                    for it in items:
                        dt = _parse_iso_dt(it.get("DateCreated"))
                        if dt is None:
                            continue
                        if oldest is None or dt < oldest:
                            oldest = dt
                        if dt < threshold_dt:
                            continue
                        sid = it.get("SeriesId")
                        if sid:
                            ids.add(str(sid))
                            prev = latest.get(str(sid))
                            if prev is None or dt > prev:
                                latest[str(sid)] = dt
                    if oldest is not None and oldest < threshold_dt:
                        return ids, latest
                    start += len(items)
                    if len(items) < page_limit:
                        return ids, latest
            except Exception as e:
                logger.error(f"Episode scan error: {e}")
                return ids, latest

    if source_parent_ids:
        parts = await asyncio.gather(*[_scan(pid) for pid in source_parent_ids])
    else:
        parts = [await _scan(None)]

    all_ids: set[str] = set()
    all_latest: Dict[str, datetime] = {}
    for ids, latest in parts:
        all_ids.update(ids)
        for sid, dt in latest.items():
            prev = all_latest.get(sid)
            if prev is None or dt > prev:
                all_latest[sid] = dt
    return all_ids, all_latest


async def _fetch_recent_by_datecreated(
    session: aiohttp.ClientSession, url: str, headers: Dict,
    base_params: Dict, threshold_dt: datetime,
    item_types: str, source_parent_ids: Optional[List[str]],
) -> List[Dict]:
    """Fetch items newer than threshold by DateCreated desc, stop early."""
    page_limit = 400

    async def _scan(parent_id: Optional[str]):
        out: List[Dict] = []
        start = 0
        while True:
            p = dict(base_params)
            p.update({"IncludeItemTypes": item_types, "Recursive": "true",
                       "SortBy": "DateCreated", "SortOrder": "Descending",
                       "StartIndex": str(start), "Limit": str(page_limit)})
            if parent_id:
                p["ParentId"] = parent_id
            try:
                async with session.get(url, params=p, headers=headers) as resp:
                    if resp.status != 200:
                        return out
                    data = await resp.json()
                    items = data.get("Items", []) if isinstance(data, dict) else []
                    if not items:
                        return out
                    oldest = None
                    for it in items:
                        dt = _parse_iso_dt(it.get("DateCreated"))
                        if dt is None:
                            continue
                        if oldest is None or dt < oldest:
                            oldest = dt
                        if dt >= threshold_dt:
                            out.append(it)
                    if oldest is not None and oldest < threshold_dt:
                        return out
                    start += len(items)
                    if len(items) < page_limit:
                        return out
            except Exception as e:
                logger.error(f"Recent scan error: {e}")
                return out

    if source_parent_ids:
        lists = await asyncio.gather(*[_scan(pid) for pid in source_parent_ids])
    else:
        lists = [await _scan(None)]
    return _deduplicate([it for lst in lists for it in lst])


async def _fetch_by_ids_chunked(
    session: aiohttp.ClientSession, url: str, headers: Dict,
    ids: List[str], fields: str,
) -> List[Dict]:
    if not ids:
        return []
    chunks = [ids[i:i+120] for i in range(0, len(ids), 120)]
    sem = asyncio.Semaphore(6)

    async def _one(chunk):
        async with sem:
            try:
                async with session.get(url, params={"Ids": ",".join(chunk), "Fields": fields}, headers=headers) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    return data.get("Items", []) if isinstance(data, dict) else []
            except Exception:
                return []

    parts = await asyncio.gather(*[_one(c) for c in chunks])
    return [it for sub in parts for it in sub]


# ---------------------------------------------------------------------------
# Core: refresh a single virtual library's full cache
# ---------------------------------------------------------------------------

async def refresh_vlib_cache(
    vlib: VirtualLibrary,
    config: AppConfig,
    *,
    session: aiohttp.ClientSession | None = None,
    user_id: str | None = None,
) -> int:
    """
    Fetch the complete item list for a virtual library from Emby,
    apply filters / TMDB merge / sort, slim, and push to proxy cache.

    Returns the number of items cached (0 if skipped or failed).
    """
    if vlib.resource_type == "rsshub":
        logger.info(f"Skipping cache refresh for RSS library '{vlib.name}'.")
        return 0

    if not config.emby_url or not config.emby_api_key:
        logger.warning(f"Cannot refresh cache for '{vlib.name}': Emby URL or API key not configured.")
        return 0

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        headers = {"X-Emby-Token": config.emby_api_key, "Accept": "application/json"}

        # Resolve user_id if not provided
        if not user_id:
            try:
                async with session.get(
                    f"{config.emby_url.rstrip('/')}/emby/Users",
                    headers=headers, timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        users = await resp.json()
                        if users:
                            user_id = users[0].get("Id")
            except Exception as e:
                logger.error(f"Failed to get Emby users: {e}")
            if not user_id:
                logger.warning(f"Cannot refresh cache for '{vlib.name}': no Emby user found.")
                return 0

        base_url = f"{config.emby_url.rstrip('/')}/emby/Users/{user_id}/Items"
        base_params: Dict[str, Any] = {
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Series,Video",
            "Fields": FETCH_FIELDS,
            "SortBy": "DateCreated",
            "SortOrder": "Descending",
        }

        # Apply resource type filter
        resource_map = {
            "collection": "CollectionIds", "tag": "TagIds",
            "person": "PersonIds", "genre": "GenreIds", "studio": "StudioIds",
        }
        if vlib.resource_type in resource_map:
            base_params[resource_map[vlib.resource_type]] = vlib.resource_id

        # Apply advanced filter (Emby-native params)
        post_filter_rules = []
        filter_match_all = True
        custom_sort_field = None
        custom_sort_order = None
        if vlib.advanced_filter_id:
            adv_filter = next((f for f in config.advanced_filters if f.id == vlib.advanced_filter_id), None)
            if adv_filter:
                emby_native_params, post_filter_rules = translate_rules(adv_filter.rules)
                base_params.update(emby_native_params)
                filter_match_all = adv_filter.match_all
                custom_sort_field = adv_filter.sort_field
                custom_sort_order = adv_filter.sort_order

        # Determine source libraries
        ignore_set = config.disabled_library_ids
        source_libs = [lid for lid in (vlib.source_libraries or []) if lid not in ignore_set]

        if vlib.resource_type == "all" and ignore_set and not source_libs:
            from admin_server import get_real_libraries_hybrid_mode
            try:
                real_libs = await get_real_libraries_hybrid_mode()
                source_libs = [lib["Id"] for lib in real_libs if lib["Id"] not in ignore_set]
            except Exception as e:
                logger.error(f"Failed to fetch real libraries: {e}")

        # --- Check for DLA optimized path ---
        dla_rules = [
            r for r in post_filter_rules
            if getattr(r, "field", None) == "DateLastMediaAdded"
            and getattr(r, "operator", None) == "greater_than"
            and getattr(r, "value", None)
        ]
        if len(dla_rules) == 1 and (filter_match_all or len(post_filter_rules) == 1):
            threshold_dt = _parse_iso_dt(dla_rules[0].value)
            if threshold_dt:
                all_items = await _do_dla_fetch(
                    session, base_url, headers, base_params,
                    threshold_dt, source_libs or None,
                    post_filter_rules, filter_match_all,
                )
                is_tmdb_merge = vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id
                if is_tmdb_merge:
                    all_items = await merge_items_by_tmdb(all_items)
                all_items = _deduplicate(all_items)
                if custom_sort_field and custom_sort_order:
                    _apply_custom_sort(all_items, custom_sort_field, custom_sort_order)
                slimmed = slim_items(all_items)
                await _push_cache_to_proxy(session, vlib.id, slimmed)
                logger.info(f"Cache refreshed (DLA) for '{vlib.name}': {len(slimmed)} items")
                return len(slimmed)

        # --- Standard full fetch ---
        all_items: List[Dict] = []
        if source_libs:
            tasks = [
                _fetch_all_pages(session, base_url, dict(base_params, ParentId=pid), headers)
                for pid in source_libs
            ]
            results = await asyncio.gather(*tasks)
            for items in results:
                all_items.extend(items)
        else:
            all_items = await _fetch_all_pages(session, base_url, base_params, headers)

        all_items = _deduplicate(all_items)

        # Apply post-filters
        if post_filter_rules:
            from proxy_handlers.handler_items import _apply_post_filter
            all_items = _apply_post_filter(all_items, post_filter_rules, filter_match_all)

        # TMDB merge
        is_tmdb_merge = vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id
        if is_tmdb_merge:
            all_items = await merge_items_by_tmdb(all_items)

        # Custom sort from advanced filter
        if custom_sort_field and custom_sort_order:
            _apply_custom_sort(all_items, custom_sort_field, custom_sort_order)

        slimmed = slim_items(all_items)
        await _push_cache_to_proxy(session, vlib.id, slimmed)
        logger.info(f"Cache refreshed for '{vlib.name}': {len(slimmed)} items")
        return len(slimmed)

    except Exception as e:
        logger.error(f"Failed to refresh cache for '{vlib.name}': {e}", exc_info=True)
        return 0
    finally:
        if own_session:
            await session.close()


async def _do_dla_fetch(
    session, url, headers, base_params,
    threshold_dt, source_pids,
    post_filter_rules, filter_match_all,
) -> List[Dict]:
    """DLA optimized: scan episodes + recent movies, then fetch full Series by IDs."""
    scan_base = {k: v for k, v in base_params.items() if k not in ("StartIndex", "Limit", "SortBy", "SortOrder")}

    ep_task = _fetch_recent_episodes_series_ids(
        session, url, headers, scan_base, threshold_dt, source_pids,
    )
    mv_task = _fetch_recent_by_datecreated(
        session, url, headers, scan_base, threshold_dt, "Movie,Video", source_pids,
    )
    (series_ids, _series_latest), movie_items = await asyncio.gather(ep_task, mv_task)

    series_items: List[Dict] = []
    if series_ids:
        series_items = await _fetch_by_ids_chunked(
            session, url, headers, list(series_ids), base_params.get("Fields", ""),
        )

    # Apply remaining post-filters (excluding the DLA rule itself)
    remaining = [r for r in post_filter_rules if getattr(r, "field", None) != "DateLastMediaAdded"]
    if remaining:
        from proxy_handlers.handler_items import _apply_post_filter
        series_items = _apply_post_filter(series_items, remaining, filter_match_all)
        movie_items = _apply_post_filter(movie_items, remaining, filter_match_all)

    return series_items + movie_items


# ---------------------------------------------------------------------------
# Batch: refresh all virtual libraries
# ---------------------------------------------------------------------------

async def refresh_all_vlib_caches(config: AppConfig) -> Dict[str, int]:
    """
    Refresh caches for all non-hidden, non-RSS virtual libraries.
    Called on startup and by scheduled jobs.
    Returns {vlib_id: item_count}.
    """
    results: Dict[str, int] = {}
    async with aiohttp.ClientSession() as session:
        # Resolve user_id once
        user_id = None
        if config.emby_url and config.emby_api_key:
            headers = {"X-Emby-Token": config.emby_api_key, "Accept": "application/json"}
            try:
                async with session.get(
                    f"{config.emby_url.rstrip('/')}/emby/Users",
                    headers=headers, timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        users = await resp.json()
                        if users:
                            user_id = users[0].get("Id")
            except Exception as e:
                logger.error(f"Failed to get Emby users for batch refresh: {e}")

        if not user_id:
            logger.warning("Cannot refresh caches: no Emby user found.")
            return results

        for vlib in config.virtual_libraries:
            if vlib.hidden:
                continue
            if vlib.resource_type in ("rsshub",):
                continue
            try:
                count = await refresh_vlib_cache(vlib, config, session=session, user_id=user_id)
                results[vlib.id] = count
            except Exception as e:
                logger.error(f"Failed to refresh cache for '{vlib.name}': {e}")
                results[vlib.id] = 0

    logger.info(f"Batch cache refresh complete: {len(results)} libraries processed")
    return results
