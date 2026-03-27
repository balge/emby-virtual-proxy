import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from aiohttp import ClientSession

from models import AppConfig, VirtualLibrary
from proxy_cache import (
    get_vlib_total_count,
    make_vlib_total_cache_key,
    set_vlib_total_count,
)
from proxy_handlers._filter_translator import translate_rules
from proxy_handlers import handler_merger
from proxy_handlers.handler_items import _apply_post_filter, _build_headers_to_forward, _fetch_items_for_parent_id

logger = logging.getLogger(__name__)


async def _fetch_users(config: AppConfig, session: ClientSession) -> List[Dict]:
    if not config.emby_url or not config.emby_api_key:
        return []
    url = f"{config.emby_url.rstrip('/')}/emby/Users"
    headers = {"X-Emby-Token": config.emby_api_key, "Accept": "application/json"}
    async with session.get(url, headers=headers) as resp:
        if resp.status != 200:
            logger.warning(f"Failed to fetch users from Emby: status={resp.status}")
            return []
        data = await resp.json()
        return data if isinstance(data, list) else []


def _is_fresh(updated_at_iso: Optional[str], max_age: timedelta) -> bool:
    if not updated_at_iso:
        return False
    try:
        updated = datetime.fromisoformat(updated_at_iso)
    except Exception:
        return False
    return (datetime.utcnow() - updated) <= max_age


async def compute_exact_vlib_total(
    *,
    config: AppConfig,
    real_emby_url: str,
    session: ClientSession,
    user_id: str,
    vlib: VirtualLibrary,
) -> int:
    """
    Compute exact total count for a virtual library for a specific user.
    This is intended to run in the background (startup/daily).
    """
    # Build base params similar to handle_virtual_library_items
    new_params: Dict[str, str] = {}
    required_fields = [
        "ProviderIds", "Genres", "Tags", "Studios", "People", "OfficialRatings",
        "CommunityRating", "ProductionYear", "VideoRange", "Container",
        "ProductionLocations", "DateLastMediaAdded", "DateCreated"
    ]
    new_params["Fields"] = ",".join(required_fields)
    new_params["Recursive"] = "true"
    new_params["IncludeItemTypes"] = "Movie,Series,Video"

    resource_map = {
        "collection": "CollectionIds",
        "tag": "TagIds",
        "person": "PersonIds",
        "genre": "GenreIds",
        "studio": "StudioIds",
    }
    if vlib.resource_type in resource_map and vlib.resource_id:
        new_params[resource_map[vlib.resource_type]] = vlib.resource_id
    elif vlib.resource_type in ("rsshub",):
        return 0

    # Advanced filter translation
    post_filter_rules = []
    filter_match_all = True
    if vlib.advanced_filter_id:
        adv_filter = next((f for f in config.advanced_filters if f.id == vlib.advanced_filter_id), None)
        if adv_filter:
            emby_native_params, post_filter_rules = translate_rules(adv_filter.rules)
            new_params.update({k: str(v) for k, v in emby_native_params.items()})
            filter_match_all = adv_filter.match_all

    if new_params.get("IsMovie") == "true":
        new_params["IncludeItemTypes"] = "Movie"
    elif new_params.get("IsSeries") == "true":
        new_params["IncludeItemTypes"] = "Series"

    is_tmdb_merge_enabled = vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id

    ignore_set = set(config.ignore_libraries) if config.ignore_libraries else set()
    source_libs = list(vlib.source_libraries) if vlib.source_libraries else []
    if source_libs and ignore_set:
        source_libs = [lid for lid in source_libs if lid not in ignore_set]

    search_url = f"{real_emby_url}/emby/Users/{user_id}/Items"
    headers = {"X-Emby-Token": config.emby_api_key, "Accept": "application/json"}

    all_items: List[Dict] = []
    if source_libs:
        tasks = [
            _fetch_items_for_parent_id(session, "GET", search_url, new_params, pid, headers)
            for pid in source_libs
        ]
        results = await asyncio.gather(*tasks)
        for items in results:
            all_items.extend(items)
    else:
        # global fetch: page through everything
        start_index = 0
        limit = 200
        while True:
            p = dict(new_params)
            p["StartIndex"] = str(start_index)
            p["Limit"] = str(limit)
            async with session.get(search_url, params=p, headers=headers) as resp:
                if resp.status != 200:
                    break
                data = await resp.json()
                batch = data.get("Items", [])
                if not batch:
                    break
                all_items.extend(batch)
                start_index += len(batch)
                if len(batch) < limit:
                    break

    # Deduplicate
    seen = set()
    deduped = []
    for it in all_items:
        iid = it.get("Id")
        if iid and iid in seen:
            continue
        if iid:
            seen.add(iid)
        deduped.append(it)

    # Post-filter
    if post_filter_rules:
        deduped = _apply_post_filter(deduped, post_filter_rules, filter_match_all)

    # TMDB merge
    if is_tmdb_merge_enabled:
        deduped = await handler_merger.merge_items_by_tmdb(deduped)

    return len(deduped)


async def refresh_vlib_totals_background(
    *,
    config: AppConfig,
    session: ClientSession,
    max_age_hours: int = 24,
    concurrency: int = 2,
) -> None:
    """
    Refresh totals in background. Uses emby_api_key, so it can run at startup/daily.
    """
    if not config.emby_url or not config.emby_api_key:
        logger.info("Skip vlib totals refresh: emby_url/api_key not configured.")
        return

    real_emby_url = config.emby_url.rstrip("/")
    users = await _fetch_users(config, session)
    user_ids = [u.get("Id") for u in users if u.get("Id")]
    if not user_ids:
        logger.info("Skip vlib totals refresh: no users found.")
        return

    vlibs = [v for v in config.virtual_libraries if not getattr(v, "hidden", False)]
    if not vlibs:
        return

    max_age = timedelta(hours=max_age_hours)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(user_id: str, vlib: VirtualLibrary):
        cache_key = make_vlib_total_cache_key(user_id, vlib.id)
        _count, updated_at = get_vlib_total_count(cache_key)
        if _is_fresh(updated_at, max_age):
            return
        async with sem:
            try:
                total = await compute_exact_vlib_total(
                    config=config,
                    real_emby_url=real_emby_url,
                    session=session,
                    user_id=user_id,
                    vlib=vlib,
                )
                set_vlib_total_count(cache_key, total)
                logger.info(f"Refreshed vlib total: user={user_id} vlib={vlib.name} ({vlib.id}) total={total}")
            except Exception as e:
                logger.warning(f"Failed to refresh vlib total for user={user_id} vlib={vlib.id}: {e}")

    tasks = [_one(uid, v) for uid in user_ids for v in vlibs]
    await asyncio.gather(*tasks)

