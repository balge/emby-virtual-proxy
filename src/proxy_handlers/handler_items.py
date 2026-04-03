# src/proxy_handlers/handler_items.py
"""
Virtual library items handler.

Architecture (after refactor):
- Full item lists are cached in vlib_items_cache (slimmed to ~300-600 bytes/item).
- All browsing requests are served by sorting + slicing from the cached full list.
- Cache is populated on startup / manual refresh / webhook / scheduled refresh.
- On cache MISS (cold start, first browse before background refresh completes),
  we fetch from Emby, slim, cache, then respond.
"""

import logging
import json
import asyncio
import random as random_module
from collections import Counter
from datetime import datetime, timezone
from fastapi import Request, Response
from aiohttp import ClientSession
from models import AppConfig, VirtualLibrary
from typing import List, Any, Dict, Optional, Tuple

from . import handler_merger, handler_views
from ._filter_translator import translate_rules
from .handler_rss import RssHandler
from proxy_cache import vlib_items_cache, slim_items

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared sort field mapping: Emby client field name → Emby item JSON key
# ---------------------------------------------------------------------------
SORT_FIELD_MAP: Dict[str, str] = {
    "SortName": "SortName",
    "DateCreated": "DateCreated",
    "PremiereDate": "PremiereDate",
    "ProductionYear": "ProductionYear",
    "CommunityRating": "CommunityRating",
    "DatePlayed": "DatePlayed",
    "DateLastContentAdded": "DateLastMediaAdded",
}

# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _build_headers_to_forward(request: Request) -> Dict[str, str]:
    """Build whitelisted headers dict for forwarding to Emby."""
    return {
        k: v for k, v in request.headers.items()
        if k.lower() in [
            'accept', 'accept-language', 'user-agent',
            'x-emby-authorization', 'x-emby-client', 'x-emby-device-name',
            'x-emby-device-id', 'x-emby-client-version', 'x-emby-language',
            'x-emby-token'
        ]
    }


def _deduplicate_by_id(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate items by Emby Item Id, preserving order."""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for item in items:
        iid = item.get("Id")
        if iid:
            if iid in seen:
                continue
            seen.add(iid)
        out.append(item)
    return out


def _resolve_advanced_filter(
    vlib: VirtualLibrary, config: AppConfig
) -> Tuple[list, bool, Optional[str], Optional[str], Dict]:
    """
    Parse advanced filter for a virtual library.
    Returns (post_filter_rules, filter_match_all, custom_sort_field, custom_sort_order, emby_native_params).
    """
    if vlib.advanced_filter_id:
        adv_filter = next(
            (f for f in config.advanced_filters if f.id == vlib.advanced_filter_id), None
        )
        if adv_filter:
            emby_native_params, post_filter_rules = translate_rules(adv_filter.rules)
            if post_filter_rules:
                logger.info(f"{len(post_filter_rules)} rules require proxy-side post-filtering.")
            return (
                post_filter_rules,
                adv_filter.match_all,
                adv_filter.sort_field,
                adv_filter.sort_order,
                emby_native_params,
            )
        else:
            logger.warning(
                f"Virtual library references advanced filter ID '{vlib.advanced_filter_id}', but it was not found."
            )
    return [], True, None, None, {}


def _extract_dla_rule(post_filter_rules: list, filter_match_all: bool):
    """Extract a single DateLastMediaAdded > threshold rule if eligible for optimized path."""
    if not post_filter_rules:
        return None
    dla_rules = [
        r for r in post_filter_rules
        if getattr(r, "field", None) == "DateLastMediaAdded"
        and getattr(r, "operator", None) == "greater_than"
        and getattr(r, "value", None)
    ]
    if len(dla_rules) == 1 and (filter_match_all or len(post_filter_rules) == 1):
        return dla_rules[0]
    return None


# ---------------------------------------------------------------------------
# Country code map for ProductionLocations matching
# ---------------------------------------------------------------------------
COUNTRY_CODE_MAP = {
    "AR": ["ar", "argentina"], "AU": ["au", "australia"],
    "BE": ["be", "belgium"], "BR": ["br", "brazil"],
    "CA": ["ca", "canada"], "CH": ["ch", "switzerland"],
    "CL": ["cl", "chile"], "CO": ["co", "colombia"],
    "CZ": ["cz", "czech", "czech republic", "czechia"],
    "DE": ["de", "germany", "deutschland"],
    "DK": ["dk", "denmark"], "EG": ["eg", "egypt"],
    "ES": ["es", "spain"], "FR": ["fr", "france"],
    "GR": ["gr", "greece"], "HK": ["hk", "hong kong"],
    "IL": ["il", "israel"], "IN": ["in", "india"],
    "IQ": ["iq", "iraq"], "IR": ["ir", "iran"],
    "IT": ["it", "italy"], "JP": ["jp", "japan"],
    "MM": ["mm", "myanmar", "burma"],
    "MO": ["mo", "macao", "macau"],
    "MX": ["mx", "mexico"], "MY": ["my", "malaysia"],
    "NL": ["nl", "netherlands", "holland"],
    "NO": ["no", "norway"], "PH": ["ph", "philippines"],
    "PK": ["pk", "pakistan"], "PL": ["pl", "poland"],
    "RU": ["ru", "russia", "russian federation"],
    "SE": ["se", "sweden"], "SG": ["sg", "singapore"],
    "TH": ["th", "thailand"], "TR": ["tr", "turkey", "turkiye"],
    "US": ["us", "usa", "united states", "united states of america"],
    "VN": ["vn", "vietnam", "viet nam"],
    "CN": ["cn", "china", "people's republic of china"],
    "GB": ["gb", "uk", "united kingdom", "great britain"],
    "TW": ["tw", "taiwan"], "NZ": ["nz", "new zealand"],
    "SA": ["sa", "saudi arabia"], "LA": ["la", "laos"],
    "KP": ["kp", "north korea", "democratic people's republic of korea"],
    "KR": ["kr", "south korea", "korea, republic of", "korea"],
    "PT": ["pt", "portugal"], "MN": ["mn", "mongolia"],
}

# ---------------------------------------------------------------------------
# Post-filter logic
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


def _check_condition(item_value: Any, operator: str, rule_value: str, field: str = "") -> bool:
    if operator not in ["is_empty", "is_not_empty"]:
        if item_value is None:
            return False
        if isinstance(item_value, list):
            if field == "ProductionLocations" and rule_value:
                code_upper = rule_value.upper()
                variants = COUNTRY_CODE_MAP.get(code_upper, [rule_value.lower()])
                if operator == "contains":
                    return any(any(v in str(el).lower() for v in variants) for el in item_value)
                if operator == "not_contains":
                    return not any(any(v in str(el).lower() for v in variants) for el in item_value)
                if operator == "equals":
                    return any(any(str(el).lower() == v for v in variants) for el in item_value)
                if operator == "not_equals":
                    return not any(any(str(el).lower() == v for v in variants) for el in item_value)
                return False
            rule_lower = str(rule_value).lower()
            if operator == "contains":
                return any(rule_lower in str(el).lower() for el in item_value)
            if operator == "not_contains":
                return not any(rule_lower in str(el).lower() for el in item_value)
            if operator == "equals":
                return any(str(el).lower() == rule_lower for el in item_value)
            if operator == "not_equals":
                return not any(str(el).lower() == rule_lower for el in item_value)
            return False
        if operator in ["greater_than", "less_than"]:
            try:
                if operator == "greater_than":
                    return float(item_value) > float(rule_value)
                if operator == "less_than":
                    return float(item_value) < float(rule_value)
            except (ValueError, TypeError):
                try:
                    iv = str(item_value).replace('Z', '').split('.')[0][:19]
                    rv = str(rule_value).replace('Z', '').split('.')[0][:19]
                    if operator == "greater_than":
                        return iv > rv
                    if operator == "less_than":
                        return iv < rv
                except Exception:
                    return False
        item_value_str = str(item_value).lower()
        rule_value_str = str(rule_value).lower()
        if operator == "equals":
            return item_value_str == rule_value_str
        if operator == "not_equals":
            return item_value_str != rule_value_str
        if operator == "contains":
            return rule_value_str in item_value_str
        if operator == "not_contains":
            return rule_value_str not in item_value_str
    if operator == "is_empty":
        return item_value is None or item_value == '' or item_value == []
    if operator == "is_not_empty":
        return item_value is not None and item_value != '' and item_value != []
    return False


def _get_value_for_rule(item: Dict[str, Any], field: str) -> Any:
    """Get item value for a rule field, with smart logic for special fields."""
    if field == "DateLastMediaAdded":
        item_type = item.get("Type", "")
        if item_type == "Series":
            val = _get_nested_value(item, "DateLastMediaAdded")
            if val:
                return val
            return _get_nested_value(item, "DateCreated")
        else:
            return _get_nested_value(item, "DateCreated")
    return _get_nested_value(item, field)


def _parse_iso_dt(value: Any) -> Optional[datetime]:
    """Parse Emby ISO datetime string into UTC datetime."""
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


def _apply_post_filter(
    items: List[Dict[str, Any]], post_filter_rules: List[Dict], match_all: bool = True
) -> List[Dict[str, Any]]:
    if not post_filter_rules:
        return items
    mode = "AND" if match_all else "OR"
    logger.info(f"Applying {len(post_filter_rules)} post-filter rules ({mode}) on {len(items)} items.")
    combiner = all if match_all else any
    return [
        item for item in items
        if combiner(
            _check_condition(_get_value_for_rule(item, rule.field), rule.operator, rule.value, rule.field)
            for rule in post_filter_rules
        )
    ]


def _apply_custom_sort(items: List[Dict[str, Any]], sort_field: str, sort_order: str) -> List[Dict[str, Any]]:
    """Sort items by the given field. sort_order: "asc" | "desc"."""
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
    logger.info(f"Custom sort applied: field={sort_field}, order={sort_order}, items={len(items)}")
    return items


# ---------------------------------------------------------------------------
# Unified sort helpers
# ---------------------------------------------------------------------------

def _sort_by_effective_last_media_added(
    items: List[Dict[str, Any]],
    series_latest_map: Dict[str, datetime],
    sort_order: str,
) -> List[Dict[str, Any]]:
    """Sort by effective last-media-added time using episode-derived dates for Series."""
    reverse = (sort_order == "desc" or str(sort_order).startswith("Desc"))

    def _k(it: Dict[str, Any]):
        if it.get("Type") == "Series":
            sid = str(it.get("Id") or "")
            dt = series_latest_map.get(sid)
            if dt is not None:
                return dt
            return (
                _parse_iso_dt(it.get("DateLastMediaAdded"))
                or _parse_iso_dt(it.get("DateCreated"))
                or datetime.min.replace(tzinfo=timezone.utc)
            )
        return _parse_iso_dt(it.get("DateCreated")) or datetime.min.replace(tzinfo=timezone.utc)

    items.sort(key=_k, reverse=reverse)
    return items


def _apply_client_sort(
    items: List[Dict[str, Any]],
    request: Request,
    series_latest_map: Optional[Dict[str, datetime]] = None,
):
    """
    Apply client-requested sort (from query params SortBy/SortOrder).
    Mutates items in-place.
    """
    sort_by = request.query_params.get("SortBy", "SortName")
    sort_order = request.query_params.get("SortOrder", "Ascending")
    primary_sort = sort_by.split(",")[0] if sort_by else "SortName"
    reverse = sort_order.startswith("Descending")

    if series_latest_map and primary_sort in ("DateCreated", "DateLastMediaAdded", "DateLastContentAdded"):
        if primary_sort in ("DateLastMediaAdded", "DateLastContentAdded"):
            _sort_by_effective_last_media_added(items, series_latest_map, sort_order)
        else:
            def _k(it: Dict[str, Any]):
                if it.get("Type") == "Series":
                    sid = str(it.get("Id") or "")
                    dt = series_latest_map.get(sid)
                    return dt or _parse_iso_dt(it.get("DateCreated")) or datetime.min.replace(tzinfo=timezone.utc)
                return _parse_iso_dt(it.get("DateCreated")) or datetime.min.replace(tzinfo=timezone.utc)
            items.sort(key=_k, reverse=reverse)
    else:
        emby_field = SORT_FIELD_MAP.get(primary_sort, "SortName")
        items.sort(key=lambda x: (x.get(emby_field) or ""), reverse=reverse)


def _apply_final_sort(
    items: List[Dict[str, Any]],
    request: Request,
    custom_sort_field: Optional[str],
    custom_sort_order: Optional[str],
    series_latest_map: Optional[Dict[str, datetime]] = None,
):
    """Unified sort pipeline."""
    _apply_client_sort(items, request, series_latest_map)
    if custom_sort_field and custom_sort_order:
        if custom_sort_field == "DateLastMediaAdded" and series_latest_map:
            _sort_by_effective_last_media_added(items, series_latest_map, custom_sort_order)
        else:
            _apply_custom_sort(items, custom_sort_field, custom_sort_order)


# ---------------------------------------------------------------------------
# Respond helper
# ---------------------------------------------------------------------------

def _make_page_response(
    items: List[Dict[str, Any]],
    start_idx: int,
    limit_count: int,
) -> Response:
    """Slice items for the requested page and return Response."""
    total_record_count = len(items)
    page_items = items[start_idx: start_idx + limit_count]
    final_data = {"Items": page_items, "TotalRecordCount": total_record_count, "StartIndex": start_idx}
    return Response(
        content=json.dumps(final_data).encode("utf-8"),
        status_code=200,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# Data fetching helpers (used for cache-miss / full refresh)
# ---------------------------------------------------------------------------

# Standard Fields to request from Emby (superset of what all clients need for poster wall)
_FETCH_FIELDS = ",".join([
    "ProviderIds", "Genres", "Tags", "Studios", "OfficialRatings",
    "CommunityRating", "ProductionYear", "VideoRange", "Container",
    "ProductionLocations", "DateLastMediaAdded", "DateCreated",
    "ImageTags", "BackdropImageTags", "SortName", "PremiereDate",
    "CriticRating", "SeriesStatus", "RunTimeTicks",
])


async def _fetch_all_items_for_parent(
    session: ClientSession, search_url: str, base_params: Dict,
    parent_id: str, headers: Dict,
) -> List[Dict]:
    """Fetch ALL items from a single parent library with pagination."""
    all_items: List[Dict] = []
    start_index = 0
    limit = 400

    fetch_params = dict(base_params)
    fetch_params["ParentId"] = parent_id
    fetch_params.pop("StartIndex", None)
    fetch_params.pop("Limit", None)

    while True:
        batch_params = dict(fetch_params)
        batch_params["StartIndex"] = str(start_index)
        batch_params["Limit"] = str(limit)

        async with session.get(search_url, params=batch_params, headers=headers) as resp:
            if resp.status != 200:
                logger.error(f"Fetch failed for ParentId={parent_id}, status={resp.status}")
                break
            data = await resp.json()
            batch = data.get("Items", [])
            if not batch:
                break
            all_items.extend(batch)
            start_index += len(batch)
            if len(batch) < limit:
                break
    return all_items


async def _fetch_all_items_global(
    session: ClientSession, search_url: str, base_params: Dict, headers: Dict,
) -> List[Dict]:
    """Fetch ALL items globally (no ParentId) with pagination."""
    all_items: List[Dict] = []
    start_index = 0
    limit = 400

    fetch_params = dict(base_params)
    fetch_params.pop("StartIndex", None)
    fetch_params.pop("Limit", None)

    while True:
        batch_params = dict(fetch_params)
        batch_params["StartIndex"] = str(start_index)
        batch_params["Limit"] = str(limit)

        async with session.get(search_url, params=batch_params, headers=headers) as resp:
            if resp.status != 200:
                logger.error(f"Global fetch failed, status={resp.status}")
                break
            data = await resp.json()
            batch = data.get("Items", [])
            if not batch:
                break
            all_items.extend(batch)
            start_index += len(batch)
            if len(batch) < limit:
                break
    return all_items


async def _fetch_recent_episodes_series_ids(
    *,
    session: ClientSession,
    search_url: str,
    headers: Dict[str, str],
    base_params: Dict[str, Any],
    threshold_dt: datetime,
    source_parent_ids: Optional[List[str]],
) -> tuple[set[str], Dict[str, datetime]]:
    """
    Use Episode.DateCreated to derive Series that updated recently.
    Stops early once items are older than threshold.
    Returns (series_ids, series_latest_map).
    """
    page_limit = 400

    async def _scan_one_parent(parent_id: Optional[str]) -> tuple[set[str], Dict[str, datetime]]:
        local_ids: set[str] = set()
        local_latest: Dict[str, datetime] = {}
        start_index = 0
        while True:
            p = dict(base_params)
            p["IncludeItemTypes"] = "Episode"
            p["Recursive"] = "true"
            p["SortBy"] = "DateCreated"
            p["SortOrder"] = "Descending"
            p["Fields"] = "DateCreated,SeriesId"
            if parent_id:
                p["ParentId"] = parent_id
            p["StartIndex"] = str(start_index)
            p["Limit"] = str(page_limit)

            async with session.get(search_url, params=p, headers=headers) as resp:
                if resp.status != 200:
                    return local_ids, local_latest
                data = await resp.json()
                items = data.get("Items", []) if isinstance(data, dict) else []
                if not items:
                    return local_ids, local_latest

                oldest_dt: Optional[datetime] = None
                for it in items:
                    created_dt = _parse_iso_dt(it.get("DateCreated"))
                    if created_dt is None:
                        continue
                    if oldest_dt is None or created_dt < oldest_dt:
                        oldest_dt = created_dt
                    if created_dt < threshold_dt:
                        continue
                    sid = it.get("SeriesId")
                    if not sid:
                        continue
                    sid_str = str(sid)
                    local_ids.add(sid_str)
                    prev = local_latest.get(sid_str)
                    if prev is None or created_dt > prev:
                        local_latest[sid_str] = created_dt

                if oldest_dt is not None and oldest_dt < threshold_dt:
                    return local_ids, local_latest
                start_index += len(items)
                if len(items) < page_limit:
                    return local_ids, local_latest

    if source_parent_ids:
        parts = await asyncio.gather(*[_scan_one_parent(pid) for pid in source_parent_ids])
    else:
        parts = [await _scan_one_parent(None)]

    series_ids: set[str] = set()
    series_latest: Dict[str, datetime] = {}
    for local_ids, local_latest in parts:
        series_ids.update(local_ids)
        for sid, dt in local_latest.items():
            prev = series_latest.get(sid)
            if prev is None or dt > prev:
                series_latest[sid] = dt
    return series_ids, series_latest


async def _fetch_items_recent_by_datecreated(
    *,
    session: ClientSession,
    search_url: str,
    headers: Dict[str, str],
    base_params: Dict[str, Any],
    threshold_dt: datetime,
    include_item_types: str,
    source_parent_ids: Optional[List[str]],
) -> List[Dict]:
    """Fetch items recent by DateCreated; stop early once older than threshold."""
    page_limit = 400

    async def _scan_one_parent(parent_id: Optional[str]) -> List[Dict]:
        out: List[Dict] = []
        start_index = 0
        while True:
            p = dict(base_params)
            p["IncludeItemTypes"] = include_item_types
            p["Recursive"] = "true"
            p["SortBy"] = "DateCreated"
            p["SortOrder"] = "Descending"
            if parent_id:
                p["ParentId"] = parent_id
            p["StartIndex"] = str(start_index)
            p["Limit"] = str(page_limit)

            async with session.get(search_url, params=p, headers=headers) as resp:
                if resp.status != 200:
                    return out
                data = await resp.json()
                items = data.get("Items", []) if isinstance(data, dict) else []
                if not items:
                    return out

                oldest_dt: Optional[datetime] = None
                for it in items:
                    created_dt = _parse_iso_dt(it.get("DateCreated"))
                    if created_dt is None:
                        continue
                    if oldest_dt is None or created_dt < oldest_dt:
                        oldest_dt = created_dt
                    if created_dt >= threshold_dt:
                        out.append(it)

                if oldest_dt is not None and oldest_dt < threshold_dt:
                    return out
                start_index += len(items)
                if len(items) < page_limit:
                    return out

    if source_parent_ids:
        lists = await asyncio.gather(*[_scan_one_parent(pid) for pid in source_parent_ids])
    else:
        lists = [await _scan_one_parent(None)]

    merged: List[Dict] = []
    seen: set[str] = set()
    for lst in lists:
        for it in lst:
            iid = it.get("Id")
            if iid and iid in seen:
                continue
            if iid:
                seen.add(iid)
            merged.append(it)
    return merged


async def _get_items_by_ids_chunked(
    *,
    session: ClientSession,
    search_url: str,
    headers: Dict[str, str],
    ids: List[str],
    fields: str,
    chunk_size: int = 120,
    max_concurrent: int = 6,
) -> List[Dict]:
    """Fetch by Ids in chunks; chunks run concurrently."""
    if not ids:
        return []
    chunks = [ids[i: i + chunk_size] for i in range(0, len(ids), chunk_size)]
    sem = asyncio.Semaphore(max_concurrent)

    async def _one_chunk(chunk: List[str]) -> List[Dict]:
        params: Dict[str, Any] = {"Ids": ",".join(chunk)}
        if fields:
            params["Fields"] = fields
        async with sem:
            async with session.get(search_url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return data.get("Items", []) if isinstance(data, dict) else []

    parts = await asyncio.gather(*[_one_chunk(c) for c in chunks])
    out: List[Dict] = []
    for sub in parts:
        out.extend(sub)
    return out


# ---------------------------------------------------------------------------
# Full data fetch → slim → cache → serve (used on cache miss)
# ---------------------------------------------------------------------------

async def _fetch_and_cache_full_items(
    *,
    request: Request,
    found_vlib: VirtualLibrary,
    user_id: str,
    real_emby_url: str,
    session: ClientSession,
    config: AppConfig,
) -> List[Dict[str, Any]]:
    """
    Fetch the complete item list for a virtual library from Emby,
    apply post-filters / TMDB merge / sort, slim, and store in cache.
    Returns the full (slimmed) item list.
    """
    headers_to_forward = _build_headers_to_forward(request)
    search_url = f"{real_emby_url}/emby/Users/{user_id}/Items"

    # Build base params
    base_params: Dict[str, Any] = {
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Series,Video",
        "Fields": _FETCH_FIELDS,
    }
    token = request.query_params.get("X-Emby-Token")
    if token:
        base_params["X-Emby-Token"] = token

    # Apply resource type
    resource_map = {
        "collection": "CollectionIds", "tag": "TagIds",
        "person": "PersonIds", "genre": "GenreIds", "studio": "StudioIds",
    }
    if found_vlib.resource_type in resource_map:
        base_params[resource_map[found_vlib.resource_type]] = found_vlib.resource_id

    # Parse advanced filter
    post_filter_rules, filter_match_all, custom_sort_field, custom_sort_order, emby_native_params = \
        _resolve_advanced_filter(found_vlib, config)
    base_params.update(emby_native_params)

    # Determine source libraries
    ignore_set = config.disabled_library_ids
    effective_source_libs = list(found_vlib.source_libraries) if found_vlib.source_libraries else []
    if effective_source_libs and ignore_set:
        effective_source_libs = [lid for lid in effective_source_libs if lid not in ignore_set]

    if found_vlib.resource_type == "all" and ignore_set and not effective_source_libs:
        from admin_server import get_real_libraries_hybrid_mode
        try:
            real_libs = await get_real_libraries_hybrid_mode()
            effective_source_libs = [lib["Id"] for lib in real_libs if lib["Id"] not in ignore_set]
        except Exception as e:
            logger.error(f"Failed to fetch real libraries for ignore filtering: {e}")

    # Check for optimized DateLastMediaAdded path
    date_last_media_added_rule = _extract_dla_rule(post_filter_rules, filter_match_all)
    series_latest_map: Optional[Dict[str, datetime]] = None

    if date_last_media_added_rule:
        threshold_dt = _parse_iso_dt(getattr(date_last_media_added_rule, "value", None))
        if threshold_dt:
            source_pids = effective_source_libs if effective_source_libs else None

            scan_base = dict(base_params)
            for k in ("StartIndex", "Limit", "SortBy", "SortOrder"):
                scan_base.pop(k, None)

            ep_task = _fetch_recent_episodes_series_ids(
                session=session, search_url=search_url, headers=headers_to_forward,
                base_params=scan_base, threshold_dt=threshold_dt, source_parent_ids=source_pids,
            )
            mv_task = _fetch_items_recent_by_datecreated(
                session=session, search_url=search_url, headers=headers_to_forward,
                base_params=scan_base, threshold_dt=threshold_dt,
                include_item_types="Movie,Video", source_parent_ids=source_pids,
            )
            (series_ids_hit, series_latest_map), movie_video_items = await asyncio.gather(ep_task, mv_task)

            series_items: List[Dict] = []
            if series_ids_hit:
                series_items = await _get_items_by_ids_chunked(
                    session=session, search_url=search_url, headers=headers_to_forward,
                    ids=list(series_ids_hit), fields=base_params.get("Fields", ""),
                )

            remaining_post_filters = [
                r for r in post_filter_rules if getattr(r, "field", None) != "DateLastMediaAdded"
            ]
            if remaining_post_filters:
                series_items = _apply_post_filter(series_items, remaining_post_filters, filter_match_all)
                movie_video_items = _apply_post_filter(movie_video_items, remaining_post_filters, filter_match_all)

            all_items = series_items + movie_video_items
            is_tmdb_merge = found_vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id
            if is_tmdb_merge:
                all_items = await handler_merger.merge_items_by_tmdb(all_items)
            all_items = _deduplicate_by_id(all_items)

            _apply_final_sort(all_items, request, custom_sort_field, custom_sort_order, series_latest_map)

            slimmed = slim_items(all_items)
            vlib_items_cache[found_vlib.id] = slimmed
            logger.info(f"Cache populated (DLA path) for '{found_vlib.name}': {len(slimmed)} items")
            return slimmed

    # --- Standard full fetch ---
    all_items: List[Dict] = []
    if effective_source_libs:
        tasks = [
            _fetch_all_items_for_parent(session, search_url, base_params, pid, headers_to_forward)
            for pid in effective_source_libs
        ]
        results = await asyncio.gather(*tasks)
        for items in results:
            all_items.extend(items)
    else:
        all_items = await _fetch_all_items_global(session, search_url, base_params, headers_to_forward)

    all_items = _deduplicate_by_id(all_items)

    if post_filter_rules:
        all_items = _apply_post_filter(all_items, post_filter_rules, filter_match_all)

    is_tmdb_merge = found_vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id
    if is_tmdb_merge:
        all_items = await handler_merger.merge_items_by_tmdb(all_items)

    _apply_final_sort(all_items, request, custom_sort_field, custom_sort_order)

    slimmed = slim_items(all_items)
    vlib_items_cache[found_vlib.id] = slimmed
    logger.info(f"Cache populated for '{found_vlib.name}': {len(slimmed)} items")
    return slimmed


# ---------------------------------------------------------------------------
# Random library handler
# ---------------------------------------------------------------------------

async def _fetch_user_genre_preferences(
    session: ClientSession, real_emby_url: str, user_id: str, headers: Dict
) -> List[str]:
    """Fetch the logged-in user's played items and extract top genre preferences."""
    url = f"{real_emby_url}/emby/Users/{user_id}/Items"
    params = {
        "IsPlayed": "true",
        "SortBy": "DatePlayed",
        "SortOrder": "Descending",
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Series",
        "Fields": "Genres",
        "Limit": "200",
    }
    genre_counter = Counter()
    try:
        async with session.get(url, params=params, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                for item in data.get("Items", []):
                    for genre in item.get("Genres", []):
                        genre_counter[genre] += 1
    except Exception as e:
        logger.warning(f"Failed to fetch user play history for recommendations: {e}")
    return [g for g, _ in genre_counter.most_common(10)]


def _weighted_random_select(items: List[Dict], preferred_genres: List[str], count: int) -> List[Dict]:
    """Select items with preference weighting."""
    if not items:
        return []
    if not preferred_genres:
        return random_module.sample(items, min(count, len(items)))

    preferred_set = set(g.lower() for g in preferred_genres)
    weighted_items = []
    for item in items:
        item_genres = [g.lower() for g in item.get("Genres", [])]
        match_count = sum(1 for g in item_genres if g in preferred_set)
        weight = 1 + match_count * 2
        weighted_items.append((item, weight))

    selected = []
    remaining = list(weighted_items)
    for _ in range(min(count, len(remaining))):
        if not remaining:
            break
        total_weight = sum(w for _, w in remaining)
        r = random_module.uniform(0, total_weight)
        cumulative = 0
        for i, (item, weight) in enumerate(remaining):
            cumulative += weight
            if cumulative >= r:
                selected.append(item)
                remaining.pop(i)
                break
    return selected


async def _handle_random_library(
    request: Request, method: str, found_vlib: VirtualLibrary,
    new_params: Dict, user_id: str, real_emby_url: str,
    session: ClientSession, config: AppConfig,
    client_start_index: str, client_limit: str,
) -> Response:
    """Handle 'random' type virtual library."""
    cache_key = f"random:{user_id}:{found_vlib.id}"
    start_idx = int(client_start_index)
    limit_count = int(client_limit)

    cached = vlib_items_cache.get(cache_key)
    if cached:
        logger.info(f"Random library '{found_vlib.name}': serving cached result for user {user_id}")
        return _make_page_response(cached, start_idx, limit_count)

    logger.info(f"Random library '{found_vlib.name}': generating new recommendations for user {user_id}")
    headers_to_forward = _build_headers_to_forward(request)
    search_url = f"{real_emby_url}/emby/Users/{user_id}/Items"

    ignore_set = config.disabled_library_ids
    source_libs = list(found_vlib.source_libraries) if found_vlib.source_libraries else []
    if source_libs and ignore_set:
        source_libs = [lid for lid in source_libs if lid not in ignore_set]

    if not source_libs:
        from admin_server import get_real_libraries_hybrid_mode
        try:
            real_libs = await get_real_libraries_hybrid_mode()
            source_libs = [lib["Id"] for lib in real_libs if lib["Id"] not in ignore_set]
        except Exception as e:
            logger.error(f"Random library: failed to fetch real libraries: {e}")

    if not source_libs:
        logger.warning(f"Random library '{found_vlib.name}': no libraries available.")
        empty = {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}
        return Response(content=json.dumps(empty).encode('utf-8'), status_code=200, media_type="application/json")

    preferred_genres = await _fetch_user_genre_preferences(session, real_emby_url, user_id, headers_to_forward)
    has_preferences = len(preferred_genres) > 0
    if has_preferences:
        logger.info(f"User {user_id} preferred genres: {preferred_genres[:5]}")
    else:
        logger.info(f"User {user_id} has no play history, will use pure random selection.")

    fetch_params = {
        "Recursive": "true",
        "IsPlayed": "false",
        "Fields": _FETCH_FIELDS,
        "SortBy": "Random",
        "SortOrder": "Ascending",
    }
    token = new_params.get("X-Emby-Token")
    if token:
        fetch_params["X-Emby-Token"] = token

    all_movies: List[Dict] = []
    all_series: List[Dict] = []

    for pid in source_libs:
        for item_type, target_list in [("Movie", all_movies), ("Series", all_series)]:
            p = dict(fetch_params)
            p["ParentId"] = pid
            p["IncludeItemTypes"] = item_type
            p["Limit"] = "300"
            try:
                async with session.get(search_url, params=p, headers=headers_to_forward) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        target_list.extend(data.get("Items", []))
            except Exception as e:
                logger.warning(f"Random fetch failed for ParentId={pid}, type={item_type}: {e}")

    # Deduplicate
    seen: set = set()
    for lst in [all_movies, all_series]:
        deduped = []
        for item in lst:
            iid = item.get("Id")
            if iid and iid not in seen:
                seen.add(iid)
                deduped.append(item)
        lst.clear()
        lst.extend(deduped)

    logger.info(f"Random pool: {len(all_movies)} movies, {len(all_series)} series")

    target_per_type = 15
    if has_preferences:
        selected_movies = _weighted_random_select(all_movies, preferred_genres, target_per_type)
        selected_series = _weighted_random_select(all_series, preferred_genres, target_per_type)
    else:
        selected_movies = random_module.sample(all_movies, min(target_per_type, len(all_movies)))
        selected_series = random_module.sample(all_series, min(target_per_type, len(all_series)))

    # Auto-fill if insufficient
    if len(selected_movies) < target_per_type:
        selected_ids = {item.get("Id") for item in selected_movies}
        remaining_movies = [m for m in all_movies if m.get("Id") not in selected_ids]
        fill_count = target_per_type - len(selected_movies)
        selected_movies.extend(random_module.sample(remaining_movies, min(fill_count, len(remaining_movies))))

    if len(selected_series) < target_per_type:
        selected_ids = {item.get("Id") for item in selected_series}
        remaining_series = [s for s in all_series if s.get("Id") not in selected_ids]
        fill_count = target_per_type - len(selected_series)
        selected_series.extend(random_module.sample(remaining_series, min(fill_count, len(remaining_series))))

    total_selected = len(selected_movies) + len(selected_series)
    if total_selected < 30:
        all_selected_ids = {item.get("Id") for item in selected_movies + selected_series}
        remaining_all = [i for i in all_movies + all_series if i.get("Id") not in all_selected_ids]
        fill_count = 30 - total_selected
        extra = random_module.sample(remaining_all, min(fill_count, len(remaining_all)))
        selected_movies.extend(extra)

    all_items = selected_movies + selected_series
    random_module.shuffle(all_items)

    logger.info(
        f"Random library '{found_vlib.name}': selected {len(selected_movies)} movies + "
        f"{len(selected_series)} series for user {user_id}"
    )

    # Slim and cache
    slimmed = slim_items(all_items)
    if slimmed:
        vlib_items_cache[cache_key] = slimmed
        vlib_items_cache[found_vlib.id] = slimmed

    return _make_page_response(slimmed, start_idx, limit_count)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def handle_virtual_library_items(
    request: Request,
    full_path: str,
    method: str,
    real_emby_url: str,
    session: ClientSession,
    config: AppConfig
) -> Response | None:
    if "/Items/Prefixes" in full_path or "/Items/Counts" in full_path or "/Items/Latest" in full_path or "/Images/" in full_path:
        return None

    params = request.query_params
    found_vlib = None

    parent_id_from_param = params.get("ParentId")
    if parent_id_from_param:
        found_vlib = next((vlib for vlib in config.virtual_libraries if vlib.id == parent_id_from_param), None)
        if found_vlib:
            logger.info(f"ParentId {parent_id_from_param} matched virtual library '{found_vlib.name}'")
        else:
            logger.debug(f"ParentId {parent_id_from_param} is not a virtual library id; passthrough request.")

    if not found_vlib and method == "GET" and 'Items' in full_path:
        path_parts = full_path.split('/')
        try:
            items_index = path_parts.index('Items')
            if items_index + 1 < len(path_parts):
                potential_path_vlib_id = path_parts[items_index + 1]
                found_vlib = next((vlib for vlib in config.virtual_libraries if vlib.id == potential_path_vlib_id), None)
        except ValueError:
            pass

    if not found_vlib:
        has_id_param = any(key.lower().endswith('id') for key in params.keys())
        if not has_id_param:
            logger.info("Detected non-standard client request (no 'Id' suffix param), returning media library view as fallback.")
            return await handler_views.handle_view_injection(request, full_path, method, real_emby_url, session, config)
        return None

    logger.info(f"Intercepted virtual library '{found_vlib.name}', type={found_vlib.resource_type}")

    user_id = params.get("UserId")
    if not user_id:
        path_parts_for_user = full_path.split('/')
        if 'Users' in path_parts_for_user:
            try:
                user_id_index = path_parts_for_user.index('Users') + 1
                if user_id_index < len(path_parts_for_user):
                    user_id = path_parts_for_user[user_id_index]
            except (ValueError, IndexError):
                pass
    if not user_id:
        return Response(content="UserId not found", status_code=400)

    if found_vlib.hidden:
        logger.info(f"Virtual library '{found_vlib.name}' is hidden; returning empty items.")
        start_idx = int(params.get("StartIndex", 0))
        empty = {"Items": [], "TotalRecordCount": 0, "StartIndex": start_idx}
        return Response(content=json.dumps(empty).encode("utf-8"), status_code=200, media_type="application/json")

    # --- RSS type: delegate to RssHandler ---
    if found_vlib.resource_type == "rsshub":
        rss_handler = RssHandler()
        response_data = await rss_handler.handle(
            request_path=full_path,
            vlib_id=found_vlib.id,
            request_params=request.query_params,
            user_id=user_id,
            session=session,
            real_emby_url=real_emby_url,
            request_headers=request.headers
        )
        start_idx = int(params.get("StartIndex", 0))
        limit_str = params.get("Limit")
        final_items = response_data.get("Items", [])
        if limit_str:
            try:
                limit = int(limit_str)
                paginated_items = final_items[start_idx: start_idx + limit]
            except (ValueError, TypeError):
                paginated_items = final_items[start_idx:]
        else:
            paginated_items = final_items[start_idx:]
        final_response = {"Items": paginated_items, "TotalRecordCount": len(final_items)}
        return Response(content=json.dumps(final_response).encode('utf-8'), media_type="application/json")

    # --- Random type ---
    if found_vlib.resource_type == "random":
        new_params: Dict[str, Any] = {}
        token = params.get("X-Emby-Token")
        if token:
            new_params["X-Emby-Token"] = token
        return await _handle_random_library(
            request, method, found_vlib, new_params, user_id,
            real_emby_url, session, config,
            params.get("StartIndex", "0"), params.get("Limit", "50"),
        )

    # --- Standard virtual library: serve from cache, fetch on miss ---
    start_idx = int(params.get("StartIndex", 0))
    limit_count = int(params.get("Limit", 50))

    cached_items = vlib_items_cache.get(found_vlib.id)
    if cached_items is not None:
        logger.info(f"Cache HIT for '{found_vlib.name}': {len(cached_items)} items, serving page start={start_idx} limit={limit_count}")
        # Re-sort in memory based on client request
        items = list(cached_items)  # shallow copy for sorting
        _apply_client_sort(items, request)
        return _make_page_response(items, start_idx, limit_count)

    # Cache MISS: fetch full data, slim, cache, then serve
    logger.info(f"Cache MISS for '{found_vlib.name}', fetching full data from Emby...")
    all_items = await _fetch_and_cache_full_items(
        request=request,
        found_vlib=found_vlib,
        user_id=user_id,
        real_emby_url=real_emby_url,
        session=session,
        config=config,
    )

    return _make_page_response(all_items, start_idx, limit_count)
