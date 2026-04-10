# src/vlib_cache_manager.py
"""
Virtual library cache manager.

Single responsibility: fetch full item lists from Emby, slim them,
and store directly into vlib_items_cache.

Runs inside the proxy process. Admin triggers refresh via HTTP API.

Triggered by:
- First user browse (lazy fill) or admin-triggered refresh (via proxy internal API)
- Admin webhook handler (via proxy internal API)
"""

import logging
import asyncio
import random as random_module
from collections import Counter
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import aiohttp
from http_client import create_client_session
from models import AppConfig, VirtualLibrary
from proxy_cache import vlib_items_cache, slim_items
from proxy_handlers._filter_translator import translate_rules
from proxy_handlers.handler_merger import merge_items_by_tmdb
from emby_api_client import get_real_libraries_hybrid_mode

logger = logging.getLogger(__name__)


def effective_cache_ttl_seconds(vlib: VirtualLibrary, config: AppConfig) -> Optional[float]:
    """
    磁盘缓存视为「新鲜」的最长秒数；None 表示不按时间过期（仅靠手动清空或定时任务覆盖）。
    与定时刷新任务相同的小时数来源：单库 cache_refresh_interval，否则全局 cache_refresh_interval。
    适用于所有走 vlib_items_cache 的虚拟库（random、collection、tag、genre 等；不含 RSS）。
    """
    h = vlib.cache_refresh_interval
    if h is None:
        h = config.cache_refresh_interval
    if h is None or int(h) <= 0:
        return None
    return float(h) * 3600.0


async def resolve_emby_user_id(
    session: aiohttp.ClientSession,
    config: AppConfig,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """Return user_id if provided; otherwise first Emby user id."""
    if user_id:
        return user_id
    if not config.emby_url or not config.emby_api_key:
        return None
    headers = {"X-Emby-Token": config.emby_api_key, "Accept": "application/json"}
    try:
        async with session.get(
            f"{config.emby_url.rstrip('/')}/emby/Users",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                users = await resp.json()
                if users:
                    return users[0].get("Id")
    except Exception as e:
        logger.error(f"resolve_emby_user_id failed: {e}")
    return None


def _server_bucket_id(server_id: Optional[str]) -> str:
    s = (server_id or "").strip()
    return s if s else "default"


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
# Random virtual library — single algorithm for browse + scheduled refresh
# ---------------------------------------------------------------------------


async def _fetch_user_genre_preferences(
    session: aiohttp.ClientSession,
    real_emby_url: str,
    user_id: str,
    headers: Dict[str, str],
) -> List[str]:
    url = f"{real_emby_url.rstrip('/')}/emby/Users/{user_id}/Items"
    params = {
        "IsPlayed": "true",
        "SortBy": "DatePlayed",
        "SortOrder": "Descending",
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Series",
        "Fields": "Genres",
        "Limit": "200",
    }
    genre_counter: Counter = Counter()
    try:
        async with session.get(
            url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                for item in data.get("Items", []):
                    for genre in item.get("Genres", []):
                        genre_counter[genre] += 1
    except Exception as e:
        logger.warning(f"Failed to fetch user play history: {e}")
    return [g for g, _ in genre_counter.most_common(10)]


def _weighted_random_select(items: List[Dict], preferred_genres: List[str], count: int) -> List[Dict]:
    if not items:
        return []
    if not preferred_genres:
        return random_module.sample(items, min(count, len(items)))
    preferred_set = set(g.lower() for g in preferred_genres)
    weighted = [
        (
            item,
            1
            + sum(
                1 for g in [g.lower() for g in item.get("Genres", [])] if g in preferred_set
            )
            * 2,
        )
        for item in items
    ]
    selected = []
    remaining = list(weighted)
    for _ in range(min(count, len(remaining))):
        if not remaining:
            break
        total_w = sum(w for _, w in remaining)
        r = random_module.uniform(0, total_w)
        cum = 0
        for i, (item, w) in enumerate(remaining):
            cum += w
            if cum >= r:
                selected.append(item)
                remaining.pop(i)
                break
    return selected


async def populate_random_vlib_items(
    vlib: VirtualLibrary,
    config: AppConfig,
    session: aiohttp.ClientSession,
    user_id: str,
    real_emby_url: str,
    *,
    headers: Dict[str, str],
    query_emby_token: Optional[str] = None,
    server_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    未播池 + 播放偏好加权 + 电影/剧集各 15 条共 30 条，slim 后写入磁盘缓存。
    定时 refresh_vlib_cache 与用户点进库共用此实现。
    """
    base = real_emby_url.rstrip("/")
    search_url = f"{base}/emby/Users/{user_id}/Items"

    ignore_set = config.disabled_library_ids
    source_libs = list(vlib.source_libraries) if vlib.source_libraries else []
    if source_libs and ignore_set:
        source_libs = [lid for lid in source_libs if lid not in ignore_set]
    if not source_libs:
        try:
            real_libs = await get_real_libraries_hybrid_mode(config=config)
            source_libs = [lib["Id"] for lib in real_libs if lib["Id"] not in ignore_set]
        except Exception as e:
            logger.error(f"Random: failed to fetch real libraries: {e}")
    if not source_libs:
        return []

    logger.info(f"Random '{vlib.name}': populate disk cache for user {user_id}")
    preferred_genres = await _fetch_user_genre_preferences(session, base, user_id, headers)
    has_prefs = len(preferred_genres) > 0

    fetch_params: Dict[str, Any] = {
        "Recursive": "true",
        "IsPlayed": "false",
        "Fields": FETCH_FIELDS,
        "SortBy": "Random",
        "SortOrder": "Ascending",
    }
    if query_emby_token:
        fetch_params["X-Emby-Token"] = query_emby_token

    all_movies: List[Dict] = []
    all_series: List[Dict] = []
    timeout = aiohttp.ClientTimeout(total=30)
    for pid in source_libs:
        for item_type, target in [("Movie", all_movies), ("Series", all_series)]:
            p = dict(fetch_params, ParentId=pid, IncludeItemTypes=item_type, Limit="300")
            try:
                async with session.get(search_url, params=p, headers=headers, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        target.extend(data.get("Items", []))
            except Exception as e:
                logger.warning(f"Random fetch failed: {e}")

    seen: set = set()
    for lst in [all_movies, all_series]:
        deduped = [
            item for item in lst if not (item.get("Id") in seen or seen.add(item.get("Id")))
        ]
        lst.clear()
        lst.extend(deduped)

    target_per_type = 15
    if has_prefs:
        sel_m = _weighted_random_select(all_movies, preferred_genres, target_per_type)
        sel_s = _weighted_random_select(all_series, preferred_genres, target_per_type)
    else:
        sel_m = random_module.sample(all_movies, min(target_per_type, len(all_movies)))
        sel_s = random_module.sample(all_series, min(target_per_type, len(all_series)))

    for sel, pool in [(sel_m, all_movies), (sel_s, all_series)]:
        if len(sel) < target_per_type:
            sel_ids = {it.get("Id") for it in sel}
            rem = [x for x in pool if x.get("Id") not in sel_ids]
            sel.extend(random_module.sample(rem, min(target_per_type - len(sel), len(rem))))

    if len(sel_m) + len(sel_s) < 30:
        all_sel_ids = {it.get("Id") for it in sel_m + sel_s}
        rem_all = [i for i in all_movies + all_series if i.get("Id") not in all_sel_ids]
        sel_m.extend(
            random_module.sample(rem_all, min(30 - len(sel_m) - len(sel_s), len(rem_all)))
        )

    all_items = sel_m + sel_s
    random_module.shuffle(all_items)

    slimmed = slim_items(all_items)
    if slimmed:
        vlib_items_cache.set_for_user(_server_bucket_id(server_id), user_id, vlib.id, slimmed)
    return slimmed


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


def _format_dt_emby_like(dt: datetime) -> str:
    """Serialize UTC datetime for JSON; consistent string sort with typical Emby payloads."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    s = dt.isoformat(timespec="milliseconds")
    if s.endswith("+00:00"):
        return s[:-6] + "Z"
    return s


# ---------------------------------------------------------------------------
# DLA optimized fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_recent_episodes_series_ids(
    session, url, headers, base_params, threshold_dt, source_parent_ids,
):
    page_limit = 400
    async def _scan(parent_id):
        ids, latest = set(), {}
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
                        if dt is None: continue
                        if oldest is None or dt < oldest: oldest = dt
                        if dt < threshold_dt: continue
                        sid = it.get("SeriesId")
                        if sid:
                            ids.add(str(sid))
                            prev = latest.get(str(sid))
                            if prev is None or dt > prev: latest[str(sid)] = dt
                    if oldest is not None and oldest < threshold_dt:
                        return ids, latest
                    start += len(items)
                    if len(items) < page_limit: return ids, latest
            except Exception as e:
                logger.error(f"Episode scan error: {e}")
                return ids, latest

    parts = await asyncio.gather(*[_scan(pid) for pid in source_parent_ids]) if source_parent_ids else [await _scan(None)]
    all_ids, all_latest = set(), {}
    for ids, latest in parts:
        all_ids.update(ids)
        for sid, dt in latest.items():
            prev = all_latest.get(sid)
            if prev is None or dt > prev: all_latest[sid] = dt
    return all_ids, all_latest


async def _fetch_recent_by_datecreated(
    session, url, headers, base_params, threshold_dt, item_types, source_parent_ids,
):
    page_limit = 400
    async def _scan(parent_id):
        out = []
        start = 0
        while True:
            p = dict(base_params)
            p.update({"IncludeItemTypes": item_types, "Recursive": "true",
                       "SortBy": "DateCreated", "SortOrder": "Descending",
                       "StartIndex": str(start), "Limit": str(page_limit)})
            if parent_id: p["ParentId"] = parent_id
            try:
                async with session.get(url, params=p, headers=headers) as resp:
                    if resp.status != 200: return out
                    data = await resp.json()
                    items = data.get("Items", []) if isinstance(data, dict) else []
                    if not items: return out
                    oldest = None
                    for it in items:
                        dt = _parse_iso_dt(it.get("DateCreated"))
                        if dt is None: continue
                        if oldest is None or dt < oldest: oldest = dt
                        if dt >= threshold_dt: out.append(it)
                    if oldest is not None and oldest < threshold_dt: return out
                    start += len(items)
                    if len(items) < page_limit: return out
            except Exception as e:
                logger.error(f"Recent scan error: {e}")
                return out

    lists = await asyncio.gather(*[_scan(pid) for pid in source_parent_ids]) if source_parent_ids else [await _scan(None)]
    return _deduplicate([it for lst in lists for it in lst])


async def _fetch_by_ids_chunked(session, url, headers, ids, fields):
    if not ids: return []
    chunks = [ids[i:i+120] for i in range(0, len(ids), 120)]
    sem = asyncio.Semaphore(6)
    async def _one(chunk):
        async with sem:
            try:
                async with session.get(url, params={"Ids": ",".join(chunk), "Fields": fields}, headers=headers) as resp:
                    if resp.status != 200: return []
                    data = await resp.json()
                    return data.get("Items", []) if isinstance(data, dict) else []
            except Exception: return []
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
    server_id: str | None = None,
) -> int:
    """
    Fetch complete item list from Emby for the given user, slim, store on disk
    under config/vlib/servers/{server_id}/users/{user_id}/{vlib_id}/. Runs inside proxy process.
    Returns item count (0 if skipped/failed).
    """
    if vlib.resource_type == "rsshub":
        return 0

    if not config.emby_url or not config.emby_api_key:
        logger.warning(f"Cannot refresh cache for '{vlib.name}': no Emby config.")
        return 0

    own_session = session is None
    if own_session:
        session = create_client_session()

    try:
        headers = {"X-Emby-Token": config.emby_api_key, "Accept": "application/json"}

        user_id = await resolve_emby_user_id(session, config, user_id)
        if not user_id:
            logger.warning(f"Cannot refresh cache for '{vlib.name}': no Emby user id.")
            return 0

        if vlib.resource_type == "random":
            base = config.emby_url.rstrip("/")
            slimmed = await populate_random_vlib_items(
                vlib,
                config,
                session,
                user_id,
                base,
                headers=dict(headers),
                server_id=server_id,
            )
            logger.info(f"Cache refreshed (random) for '{vlib.name}': {len(slimmed)} items")
            return len(slimmed)

        base_url = f"{config.emby_url.rstrip('/')}/emby/Users/{user_id}/Items"
        base_params: Dict[str, Any] = {
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Series,Video",
            "Fields": FETCH_FIELDS,
            "SortBy": "DateCreated",
            "SortOrder": "Descending",
        }

        resource_map = {
            "collection": "CollectionIds", "tag": "TagIds",
            "person": "PersonIds", "genre": "GenreIds", "studio": "StudioIds",
        }
        res_ids = vlib.resolved_resource_ids() if vlib.resource_type in resource_map else []
        rp_name = resource_map.get(vlib.resource_type)
        if rp_name and len(res_ids) == 1:
            base_params[rp_name] = res_ids[0]
        elif rp_name and len(res_ids) == 0:
            logger.warning(f"No resource ids for '{vlib.name}' type={vlib.resource_type}; skip cache refresh.")
            return 0

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

        ignore_set = config.disabled_library_ids
        source_libs = [lid for lid in (vlib.source_libraries or []) if lid not in ignore_set]

        if vlib.resource_type == "all" and ignore_set and not source_libs:
            try:
                real_libs = await get_real_libraries_hybrid_mode(config=config)
                source_libs = [lib["Id"] for lib in real_libs if lib["Id"] not in ignore_set]
            except Exception as e:
                logger.error(f"Failed to fetch real libraries: {e}")

        # --- DLA optimized path ---
        dla_rules = [
            r for r in post_filter_rules
            if getattr(r, "field", None) == "DateLastMediaAdded"
            and getattr(r, "operator", None) == "greater_than"
            and getattr(r, "value", None)
        ]
        if (
            len(dla_rules) == 1
            and (filter_match_all or len(post_filter_rules) == 1)
            and len(res_ids) <= 1
        ):
            threshold_dt = _parse_iso_dt(dla_rules[0].value)
            if threshold_dt:
                all_items = await _do_dla_fetch(
                    session, base_url, headers, base_params,
                    threshold_dt, source_libs or None,
                    post_filter_rules, filter_match_all,
                )
                if vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id:
                    all_items = await merge_items_by_tmdb(all_items)
                all_items = _deduplicate(all_items)
                if custom_sort_field and custom_sort_order:
                    _apply_custom_sort(all_items, custom_sort_field, custom_sort_order)
                else:
                    # DLA 合并顺序为「剧集块 + 电影块」，且 Ids 批量接口不保证时间序；未配置排序时
                    # 默认按「最近媒体入库」降序，与 DateLastMediaAdded 规则语义一致。
                    _apply_custom_sort(all_items, "DateLastMediaAdded", "desc")
                slimmed = slim_items(all_items)
                vlib_items_cache.set_for_user(_server_bucket_id(server_id), user_id, vlib.id, slimmed)
                logger.info(f"Cache refreshed (DLA) for '{vlib.name}': {len(slimmed)} items")
                return len(slimmed)

        # --- Standard full fetch ---
        all_items: List[Dict] = []
        if rp_name and len(res_ids) > 1:
            if source_libs:
                tasks = [
                    _fetch_all_pages(session, base_url, dict(base_params, ParentId=pid, **{rp_name: rid}), headers)
                    for pid in source_libs
                    for rid in res_ids
                ]
                results = await asyncio.gather(*tasks)
                for items in results:
                    all_items.extend(items)
            else:
                for rid in res_ids:
                    p = dict(base_params)
                    p[rp_name] = rid
                    all_items.extend(await _fetch_all_pages(session, base_url, p, headers))
            all_items = _deduplicate(all_items)
        elif source_libs:
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

        if post_filter_rules:
            from proxy_handlers.handler_items import _apply_post_filter
            all_items = _apply_post_filter(all_items, post_filter_rules, filter_match_all)

        if vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id:
            all_items = await merge_items_by_tmdb(all_items)

        if custom_sort_field and custom_sort_order:
            _apply_custom_sort(all_items, custom_sort_field, custom_sort_order)

        slimmed = slim_items(all_items)
        vlib_items_cache.set_for_user(_server_bucket_id(server_id), user_id, vlib.id, slimmed)
        logger.info(f"Cache refreshed for '{vlib.name}': {len(slimmed)} items")
        return len(slimmed)

    except Exception as e:
        logger.error(f"Failed to refresh cache for '{vlib.name}': {e}", exc_info=True)
        return 0
    finally:
        if own_session:
            await session.close()


def _apply_dla_series_latest_episode_dates(series_items: List[Dict], latest_episode_utc: Dict[str, datetime]) -> None:
    """
    DLA 用分集扫描判定「最近入库」；按 Ids 拉回的 Series 上 DateLastMediaAdded 可能滞后于库内实际，
    排序与写入 DB 的 slim 仍沿用旧值。这里用扫描到的最新分集 DateCreated 写回 Series.DateLastMediaAdded。
    """
    for it in series_items:
        if it.get("Type") != "Series":
            continue
        sid = it.get("Id")
        if not sid:
            continue
        dt = latest_episode_utc.get(str(sid))
        if dt is None:
            continue
        it["DateLastMediaAdded"] = _format_dt_emby_like(dt)


async def _do_dla_fetch(session, url, headers, base_params, threshold_dt, source_pids, post_filter_rules, filter_match_all):
    scan_base = {k: v for k, v in base_params.items() if k not in ("StartIndex", "Limit", "SortBy", "SortOrder")}
    ep_task = _fetch_recent_episodes_series_ids(session, url, headers, scan_base, threshold_dt, source_pids)
    mv_task = _fetch_recent_by_datecreated(session, url, headers, scan_base, threshold_dt, "Movie,Video", source_pids)
    (series_ids, latest_by_series), movie_items = await asyncio.gather(ep_task, mv_task)
    series_items = []
    if series_ids:
        series_items = await _fetch_by_ids_chunked(session, url, headers, list(series_ids), base_params.get("Fields", ""))
        _apply_dla_series_latest_episode_dates(series_items, latest_by_series)
    remaining = [r for r in post_filter_rules if getattr(r, "field", None) != "DateLastMediaAdded"]
    if remaining:
        from proxy_handlers.handler_items import _apply_post_filter
        series_items = _apply_post_filter(series_items, remaining, filter_match_all)
        movie_items = _apply_post_filter(movie_items, remaining, filter_match_all)
    return series_items + movie_items


# ---------------------------------------------------------------------------
# Batch refresh all
# ---------------------------------------------------------------------------

async def refresh_all_vlib_caches(config: AppConfig) -> Dict[str, int]:
    """Refresh on-disk cache for all non-hidden, non-RSS vlibs (first Emby user only)."""
    results: Dict[str, int] = {}
    async with create_client_session() as session:
        user_id = await resolve_emby_user_id(session, config, None)
        if not user_id:
            logger.warning("Cannot refresh caches: no Emby user found.")
            return results

        for vlib in config.virtual_libraries:
            if vlib.hidden or vlib.resource_type == "rsshub":
                continue
            try:
                count = await refresh_vlib_cache(vlib, config, session=session, user_id=user_id)
                results[vlib.id] = count
            except Exception as e:
                logger.error(f"Failed to refresh cache for '{vlib.name}': {e}")
                results[vlib.id] = 0

    logger.info(f"Batch cache refresh complete: {len(results)} libraries processed")
    return results
