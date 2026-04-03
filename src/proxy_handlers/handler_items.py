# src/proxy_handlers/handler_items.py

import logging
import json
import asyncio
import random as random_module
from collections import Counter
from datetime import datetime, timezone
from fastapi import Request, Response
from aiohttp import ClientSession
from models import AppConfig, AdvancedFilter, VirtualLibrary
from typing import List, Any, Dict, Optional, Tuple

from . import handler_merger, handler_views
from ._filter_translator import translate_rules
from .handler_rss import RssHandler
from proxy_cache import (
    vlib_items_cache,
    get_vlib_page_cache,
    set_vlib_page_cache,
)
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

def _build_vlib_page_context_key(request: Request, user_id: str) -> str:
    """Build a pagination-insensitive context key for virtual library page cache."""
    exclude = {"StartIndex", "Limit", "X-Emby-Token", "api_key"}
    pairs = sorted(
        (k, v) for k, v in request.query_params.items() if k not in exclude
    )
    return f"user:{user_id}:q:{tuple(pairs)}"


def _resolve_cache_ttl_seconds(vlib: VirtualLibrary, config: AppConfig) -> Optional[int]:
    hours = vlib.cache_refresh_interval
    if hours is None:
        hours = config.cache_refresh_interval
    if hours is None:
        return None
    try:
        h = int(hours)
    except Exception:
        return None
    return h * 3600 if h > 0 else None


def _cache_current_page(
    *,
    vlib_id: str,
    context_key: str,
    start_idx: int,
    limit_count: int,
    total_record_count: int,
    page_items: List[Dict[str, Any]],
    ttl_seconds: Optional[int] = None,
):
    """Cache current page only."""
    if limit_count <= 0:
        return
    data = {"Items": page_items, "TotalRecordCount": total_record_count, "StartIndex": start_idx}
    set_vlib_page_cache(vlib_id, context_key, start_idx, limit_count, data, ttl_seconds=ttl_seconds)


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
    When series_latest_map is available, DateCreated/DateLastMediaAdded sorts use
    episode-derived dates for Series.  Mutates items in-place.
    """
    sort_by = request.query_params.get("SortBy", "SortName")
    sort_order = request.query_params.get("SortOrder", "Ascending")
    primary_sort = sort_by.split(",")[0] if sort_by else "SortName"
    reverse = sort_order.startswith("Descending")

    if series_latest_map and primary_sort in ("DateCreated", "DateLastMediaAdded", "DateLastContentAdded"):
        if primary_sort in ("DateLastMediaAdded", "DateLastContentAdded"):
            _sort_by_effective_last_media_added(items, series_latest_map, sort_order)
        else:
            # DateCreated: for Series use episode-derived date
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
    """
    Unified sort pipeline:
    1. Apply client sort from request params
    2. If advanced filter has custom sort, override with that
    """
    _apply_client_sort(items, request, series_latest_map)
    if custom_sort_field and custom_sort_order:
        if custom_sort_field == "DateLastMediaAdded" and series_latest_map:
            _sort_by_effective_last_media_added(items, series_latest_map, custom_sort_order)
        else:
            _apply_custom_sort(items, custom_sort_field, custom_sort_order)


# ---------------------------------------------------------------------------
# Unified paginate + cache + respond
# ---------------------------------------------------------------------------

def _paginate_and_respond(
    *,
    items: List[Dict[str, Any]],
    start_idx: int,
    limit_count: int,
    vlib_id: str,
    page_ctx_key: str,
    page_ttl_seconds: Optional[int],
    cache_page: bool = True,
) -> Response:
    """Slice items for the requested page, cache, update vlib_items_cache, return Response."""
    total_record_count = len(items)
    page_items = items[start_idx: start_idx + limit_count]

    if cache_page and limit_count > 0:
        _cache_current_page(
            vlib_id=vlib_id,
            context_key=page_ctx_key,
            start_idx=start_idx,
            limit_count=limit_count,
            total_record_count=total_record_count,
            page_items=page_items,
            ttl_seconds=page_ttl_seconds,
        )

    if page_items:
        vlib_items_cache[vlib_id] = page_items

    final_data = {"Items": page_items, "TotalRecordCount": total_record_count, "StartIndex": start_idx}
    return Response(
        content=json.dumps(final_data).encode("utf-8"),
        status_code=200,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# Data fetching helpers
# ---------------------------------------------------------------------------

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
                    logger.warning(f"Episode scan failed (ParentId={parent_id}), status={resp.status}")
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
                    logger.warning(f"Recent scan failed (ParentId={parent_id}), status={resp.status}")
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


async def _fetch_items_for_parent_id(
    session: ClientSession, method: str, search_url: str,
    base_params: Dict, parent_id: str, headers: Dict
) -> List[Dict]:
    """Fetch all items from a single parent library with pagination."""
    all_items = []
    start_index = 0
    limit = 200
    fetch_params = base_params.copy()
    fetch_params["ParentId"] = parent_id
    fetch_params.pop("StartIndex", None)
    fetch_params.pop("Limit", None)

    while True:
        batch_params = fetch_params.copy()
        batch_params["StartIndex"] = str(start_index)
        batch_params["Limit"] = str(limit)

        async with session.request(method, search_url, params=batch_params, headers=headers) as resp:
            if resp.status != 200:
                logger.error(f"Source library fetch failed for ParentId={parent_id}, status={resp.status}")
                break
            batch_data = await resp.json()
            batch_items = batch_data.get("Items", [])
            if not batch_items:
                break
            all_items.extend(batch_items)
            start_index += len(batch_items)
            if len(batch_items) < limit:
                break
    return all_items


async def _fetch_items_for_parent_id_up_to(
    session: ClientSession,
    method: str,
    search_url: str,
    base_params: Dict,
    parent_id: str,
    headers: Dict,
    max_items: int,
) -> tuple[List[Dict], int | None]:
    """Fetch items from a single parent library up to max_items. Returns (items, total_record_count)."""
    if max_items <= 0:
        return [], None

    collected: List[Dict] = []
    start_index = 0
    limit = 400
    total_record_count: int | None = None

    fetch_params = base_params.copy()
    fetch_params["ParentId"] = parent_id
    fetch_params.pop("StartIndex", None)
    fetch_params.pop("Limit", None)

    while len(collected) < max_items:
        batch_params = fetch_params.copy()
        batch_params["StartIndex"] = str(start_index)
        batch_params["Limit"] = str(min(limit, max_items - len(collected)))

        async with session.request(method, search_url, params=batch_params, headers=headers) as resp:
            if resp.status != 200:
                logger.error(f"Source library fetch failed for ParentId={parent_id}, status={resp.status}")
                break
            batch_data = await resp.json()
            if total_record_count is None:
                trc = batch_data.get("TotalRecordCount")
                if isinstance(trc, int):
                    total_record_count = trc
            batch_items = batch_data.get("Items", [])
            if not batch_items:
                break
            collected.extend(batch_items)
            start_index += len(batch_items)
            if len(batch_items) < int(batch_params["Limit"]):
                break

    return collected, total_record_count


# ---------------------------------------------------------------------------
# Optimized DateLastMediaAdded flow (unified for scoped and non-scoped)
# ---------------------------------------------------------------------------

async def _handle_dla_optimized_flow(
    *,
    request: Request,
    method: str,
    found_vlib: VirtualLibrary,
    new_params: Dict,
    user_id: str,
    session: ClientSession,
    search_url: str,
    headers_to_forward: Dict,
    source_parent_ids: Optional[List[str]],
    threshold_dt: datetime,
    post_filter_rules: list,
    filter_match_all: bool,
    is_tmdb_merge_enabled: bool,
    custom_sort_field: Optional[str],
    custom_sort_order: Optional[str],
    client_start_index: str,
    client_limit: str,
    page_ctx_key: str,
    page_ttl_seconds: Optional[int],
) -> Response:
    """Unified DateLastMediaAdded optimized path for both scoped and non-scoped requests."""
    logger.info(
        f"Optimized DateLastMediaAdded: threshold={threshold_dt.isoformat()}, "
        f"scoped={'yes' if source_parent_ids else 'no'}"
    )

    scan_base_params = dict(new_params)
    for k in ("StartIndex", "Limit", "SortBy", "SortOrder"):
        scan_base_params.pop(k, None)

    ep_task = _fetch_recent_episodes_series_ids(
        session=session,
        search_url=search_url,
        headers=headers_to_forward,
        base_params=scan_base_params,
        threshold_dt=threshold_dt,
        source_parent_ids=source_parent_ids,
    )
    mv_task = _fetch_items_recent_by_datecreated(
        session=session,
        search_url=search_url,
        headers=headers_to_forward,
        base_params=scan_base_params,
        threshold_dt=threshold_dt,
        include_item_types="Movie,Video",
        source_parent_ids=source_parent_ids,
    )
    (series_ids_hit, series_latest_map), movie_video_items = await asyncio.gather(ep_task, mv_task)

    series_items: List[Dict] = []
    if series_ids_hit:
        series_items = await _get_items_by_ids_chunked(
            session=session,
            search_url=search_url,
            headers=headers_to_forward,
            ids=list(series_ids_hit),
            fields=new_params.get("Fields", ""),
        )

    remaining_post_filters = [
        r for r in post_filter_rules if getattr(r, "field", None) != "DateLastMediaAdded"
    ]
    if remaining_post_filters:
        series_items = _apply_post_filter(series_items, remaining_post_filters, filter_match_all)
        movie_video_items = _apply_post_filter(movie_video_items, remaining_post_filters, filter_match_all)

    combined = series_items + movie_video_items
    if is_tmdb_merge_enabled:
        combined = await handler_merger.merge_items_by_tmdb(combined)

    combined = _deduplicate_by_id(combined)

    _apply_final_sort(combined, request, custom_sort_field, custom_sort_order, series_latest_map)

    start_idx = int(client_start_index)
    limit_count = int(client_limit)
    return _paginate_and_respond(
        items=combined,
        start_idx=start_idx,
        limit_count=limit_count,
        vlib_id=found_vlib.id,
        page_ctx_key=page_ctx_key,
        page_ttl_seconds=page_ttl_seconds,
        cache_page=(method == "GET"),
    )


# ---------------------------------------------------------------------------
# Source library scoped request handler
# ---------------------------------------------------------------------------

async def _handle_source_library_scoped_request(
    request: Request, method: str, found_vlib: VirtualLibrary,
    new_params: Dict, user_id: str, real_emby_url: str,
    session: ClientSession, config: AppConfig,
    client_start_index: str, client_limit: str,
    page_ctx_key: str, page_ttl_seconds: Optional[int],
) -> Response:
    """
    Handle virtual library requests scoped to specific real libraries.
    Issues parallel requests per source library, merges results, then applies
    post-filter / TMDB merge / pagination in the proxy.
    """
    logger.info(f"Source library scoping enabled for '{found_vlib.name}': {found_vlib.source_libraries}")

    headers_to_forward = _build_headers_to_forward(request)
    search_url = f"{real_emby_url}/emby/Users/{user_id}/Items"

    # Parse advanced filter
    post_filter_rules, filter_match_all, custom_sort_field, custom_sort_order, emby_native_params = \
        _resolve_advanced_filter(found_vlib, config)
    new_params.update(emby_native_params)

    if new_params.get("IsMovie") == "true":
        new_params["IncludeItemTypes"] = "Movie"
    elif new_params.get("IsSeries") == "true":
        new_params["IncludeItemTypes"] = "Series"

    is_tmdb_merge_enabled = found_vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id

    # Check for optimized DateLastMediaAdded path
    date_last_media_added_rule = _extract_dla_rule(post_filter_rules, filter_match_all)
    if date_last_media_added_rule:
        threshold_dt = _parse_iso_dt(getattr(date_last_media_added_rule, "value", None))
        if threshold_dt:
            return await _handle_dla_optimized_flow(
                request=request,
                method=method,
                found_vlib=found_vlib,
                new_params=new_params,
                user_id=user_id,
                session=session,
                search_url=search_url,
                headers_to_forward=headers_to_forward,
                source_parent_ids=list(found_vlib.source_libraries),
                threshold_dt=threshold_dt,
                post_filter_rules=post_filter_rules,
                filter_match_all=filter_match_all,
                is_tmdb_merge_enabled=is_tmdb_merge_enabled,
                custom_sort_field=custom_sort_field,
                custom_sort_order=custom_sort_order,
                client_start_index=client_start_index,
                client_limit=client_limit,
                page_ctx_key=page_ctx_key,
                page_ttl_seconds=page_ttl_seconds,
            )

    # --- Normal scoped path: fetch from each source library, merge, sort, paginate ---
    enable_total = request.query_params.get("EnableTotalRecordCount", "true").lower() != "false"
    start_idx = int(client_start_index)
    limit_count = int(client_limit)

    async def _fetch_merge_sort_paginate(per_lib_target: int) -> tuple[list[dict], int, list[dict]]:
        tasks = [
            _fetch_items_for_parent_id_up_to(
                session, method, search_url, new_params, pid, headers_to_forward, per_lib_target
            )
            for pid in found_vlib.source_libraries
        ]
        results = await asyncio.gather(*tasks)
        fetched: List[Dict] = []
        summed_totals_local = 0
        for items, trc in results:
            fetched.extend(items)
            if enable_total and isinstance(trc, int):
                summed_totals_local += trc

        logger.info(
            f"Source library scoped fetch complete: {len(fetched)} fetched items "
            f"from {len(found_vlib.source_libraries)} libraries (per_lib_target={per_lib_target})."
        )

        merged = _deduplicate_by_id(fetched)

        if post_filter_rules:
            merged = _apply_post_filter(merged, post_filter_rules, filter_match_all)
        if is_tmdb_merge_enabled:
            merged = await handler_merger.merge_items_by_tmdb(merged)

        _apply_final_sort(merged, request, custom_sort_field, custom_sort_order)

        page = merged[start_idx: start_idx + limit_count]
        total_rc = summed_totals_local if enable_total else 0
        return page, total_rc, merged

    buffer = max(50, limit_count)
    per_lib_target = start_idx + limit_count + buffer
    paginated_items, total_record_count, merged_items = await _fetch_merge_sort_paginate(per_lib_target)

    # Retry once with larger target if page is under-filled
    if len(paginated_items) < limit_count:
        retry_target = per_lib_target * 2
        logger.info(
            f"Scoped result under-filled (page={len(paginated_items)}/{limit_count}), "
            f"retrying with per_lib_target={retry_target}."
        )
        paginated_items, total_record_count, merged_items = await _fetch_merge_sort_paginate(retry_target)

    if paginated_items:
        vlib_items_cache[found_vlib.id] = paginated_items

    final_data = {
        "Items": paginated_items,
        "TotalRecordCount": total_record_count,
        "StartIndex": start_idx,
    }
    if method == "GET" and limit_count > 0:
        _cache_current_page(
            vlib_id=found_vlib.id,
            context_key=page_ctx_key,
            start_idx=start_idx,
            limit_count=limit_count,
            total_record_count=total_record_count,
            page_items=paginated_items,
            ttl_seconds=page_ttl_seconds,
        )
    logger.info(f"Source library scoped result: total={total_record_count}, page={len(paginated_items)}")

    return Response(
        content=json.dumps(final_data).encode('utf-8'),
        status_code=200,
        media_type="application/json",
    )


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
    page_ctx_key: str, page_ttl_seconds: Optional[int]
) -> Response:
    """Handle 'random' type virtual library."""
    cache_key = f"random:{user_id}:{found_vlib.id}"
    start_idx = int(client_start_index)
    limit_count = int(client_limit)

    if method == "GET" and limit_count > 0:
        cached_page = get_vlib_page_cache(found_vlib.id, page_ctx_key, start_idx, limit_count)
        if cached_page:
            logger.info(f"Random page cache HIT: vlib={found_vlib.id}, user={user_id}, start={start_idx}, limit={limit_count}")
            return Response(content=json.dumps(cached_page).encode("utf-8"), status_code=200, media_type="application/json")

    cached = vlib_items_cache.get(cache_key)
    if cached:
        logger.info(f"Random library '{found_vlib.name}': serving cached result for user {user_id}")
        all_items = cached
    else:
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
            "Fields": "Genres,ProviderIds,ProductionYear,CommunityRating,DateCreated,ImageTags",
            "SortBy": "Random",
            "SortOrder": "Ascending",
        }
        token = new_params.get("X-Emby-Token")
        if token:
            fetch_params["X-Emby-Token"] = token

        all_movies = []
        all_series = []

        for pid in source_libs:
            for item_type, target_list in [("Movie", all_movies), ("Series", all_series)]:
                p = fetch_params.copy()
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
        seen = set()
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

        if all_items:
            vlib_items_cache[cache_key] = all_items
            vlib_items_cache[found_vlib.id] = all_items

    return _paginate_and_respond(
        items=all_items,
        start_idx=start_idx,
        limit_count=limit_count,
        vlib_id=found_vlib.id,
        page_ctx_key=page_ctx_key,
        page_ttl_seconds=page_ttl_seconds,
        cache_page=(method == "GET"),
    )


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

    logger.info(f"Intercepted virtual library '{found_vlib.name}', starting optimized filter flow.")

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

    # --- Page cache check ---
    page_start = int(params.get("StartIndex", 0))
    page_limit = int(params.get("Limit", 50))
    page_ctx_key = _build_vlib_page_context_key(request, user_id)
    page_ttl_seconds = _resolve_cache_ttl_seconds(found_vlib, config)
    if method == "GET" and found_vlib.resource_type not in ("rsshub", "random") and page_limit > 0:
        cached = get_vlib_page_cache(found_vlib.id, page_ctx_key, page_start, page_limit)
        if cached:
            logger.info(f"Vlib page cache HIT: vlib={found_vlib.id}, start={page_start}, limit={page_limit}")
            return Response(content=json.dumps(cached).encode("utf-8"), status_code=200, media_type="application/json")
        logger.info(f"Vlib page cache MISS: vlib={found_vlib.id}, start={page_start}, limit={page_limit}")

    # --- Build request params ---
    new_params = {}
    client_start_index = params.get("StartIndex", "0")
    client_limit = params.get("Limit", "50")
    safe_params_to_inherit = [
        "SortBy", "SortOrder", "Fields", "EnableImageTypes", "ImageTypeLimit",
        "EnableTotalRecordCount", "X-Emby-Token", "StartIndex", "Limit"
    ]
    for key in safe_params_to_inherit:
        if key in params:
            new_params[key] = params[key]

    required_fields = [
        "ProviderIds", "Genres", "Tags", "Studios", "People", "OfficialRatings",
        "CommunityRating", "ProductionYear", "VideoRange", "Container",
        "ProductionLocations", "DateLastMediaAdded", "DateCreated"
    ]
    if "Fields" in new_params:
        existing_fields = set(new_params["Fields"].split(','))
        missing_fields = [f for f in required_fields if f not in existing_fields]
        if missing_fields:
            new_params["Fields"] += "," + ",".join(missing_fields)
    else:
        new_params["Fields"] = ",".join(required_fields)

    new_params["Recursive"] = "true"
    new_params["IncludeItemTypes"] = "Movie,Series,Video"

    # --- RSS type: delegate to RssHandler ---
    resource_map = {"collection": "CollectionIds", "tag": "TagIds", "person": "PersonIds", "genre": "GenreIds", "studio": "StudioIds"}
    if found_vlib.resource_type in resource_map:
        new_params[resource_map[found_vlib.resource_type]] = found_vlib.resource_id
    elif found_vlib.resource_type == "rsshub":
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
        return await _handle_random_library(
            request, method, found_vlib, new_params, user_id,
            real_emby_url, session, config, client_start_index, client_limit,
            page_ctx_key, page_ttl_seconds
        )

    # --- Source library scoping ---
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

    if effective_source_libs and found_vlib.resource_type != "rsshub":
        found_vlib_copy = found_vlib.model_copy(update={"source_libraries": effective_source_libs})
        return await _handle_source_library_scoped_request(
            request, method, found_vlib_copy, new_params, user_id,
            real_emby_url, session, config, client_start_index, client_limit,
            page_ctx_key, page_ttl_seconds,
        )


    # --- Non-scoped paths: advanced filter, standard, TMDB merge ---
    post_filter_rules, filter_match_all, custom_sort_field, custom_sort_order, emby_native_params = \
        _resolve_advanced_filter(found_vlib, config)
    new_params.update(emby_native_params)

    date_last_media_added_rule = _extract_dla_rule(post_filter_rules, filter_match_all)

    if new_params.get("IsMovie") == "true":
        new_params["IncludeItemTypes"] = "Movie"
    elif new_params.get("IsSeries") == "true":
        new_params["IncludeItemTypes"] = "Series"

    is_tmdb_merge_enabled = found_vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id

    search_url = f"{real_emby_url}/emby/Users/{user_id}/Items"
    headers_to_forward = _build_headers_to_forward(request)

    logger.debug(f"Final optimized request to Emby: URL={search_url}, Params={new_params}")

    # --- Optimized DateLastMediaAdded flow ---
    if date_last_media_added_rule:
        threshold_dt = _parse_iso_dt(getattr(date_last_media_added_rule, "value", None))
        if threshold_dt:
            # Scope to source libraries if configured (respect disabled list)
            source_parent_ids = list(found_vlib.source_libraries) if found_vlib.source_libraries else None
            if source_parent_ids and ignore_set:
                source_parent_ids = [lid for lid in source_parent_ids if lid not in ignore_set]
            if source_parent_ids and len(source_parent_ids) == 0:
                source_parent_ids = None

            return await _handle_dla_optimized_flow(
                request=request,
                method=method,
                found_vlib=found_vlib,
                new_params=new_params,
                user_id=user_id,
                session=session,
                search_url=search_url,
                headers_to_forward=headers_to_forward,
                source_parent_ids=source_parent_ids,
                threshold_dt=threshold_dt,
                post_filter_rules=post_filter_rules,
                filter_match_all=filter_match_all,
                is_tmdb_merge_enabled=is_tmdb_merge_enabled,
                custom_sort_field=custom_sort_field,
                custom_sort_order=custom_sort_order,
                client_start_index=client_start_index,
                client_limit=client_limit,
                page_ctx_key=page_ctx_key,
                page_ttl_seconds=page_ttl_seconds,
            )

    # --- Standard pagination path (no TMDB merge, or has post-filter rules) ---
    if not is_tmdb_merge_enabled or post_filter_rules:
        if is_tmdb_merge_enabled and post_filter_rules:
            logger.warning("TMDB merge enabled but post-filter rules exist; merge will be partial (current page only).")

        async with session.request(method, search_url, params=new_params, headers=headers_to_forward) as resp:
            if resp.status != 200:
                content = await resp.read()
                return Response(content=content, status_code=resp.status, headers=resp.headers)

            content = await resp.read()
            response_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in ('transfer-encoding', 'connection', 'content-encoding', 'content-length')
            }

            if "application/json" in resp.headers.get("Content-Type", ""):
                try:
                    data = json.loads(content)
                    items_list = data.get("Items", [])

                    if post_filter_rules:
                        items_list = _apply_post_filter(items_list, post_filter_rules, filter_match_all)
                    if is_tmdb_merge_enabled:
                        items_list = await handler_merger.merge_items_by_tmdb(items_list)
                    if custom_sort_field and custom_sort_order:
                        items_list = _apply_custom_sort(items_list, custom_sort_field, custom_sort_order)

                    data["Items"] = items_list
                    logger.info(f"Native filter/merge done. Emby total: {data.get('TotalRecordCount')}, page items: {len(items_list)}")

                    if method == "GET":
                        try:
                            start_idx = int(data.get("StartIndex", client_start_index))
                            total_record_count = int(data.get("TotalRecordCount", len(items_list)))
                            _cache_current_page(
                                vlib_id=found_vlib.id,
                                context_key=page_ctx_key,
                                start_idx=start_idx,
                                limit_count=page_limit,
                                total_record_count=total_record_count,
                                page_items=items_list,
                                ttl_seconds=page_ttl_seconds,
                            )
                        except Exception:
                            pass

                    if items_list:
                        vlib_items_cache[found_vlib.id] = items_list

                    content = json.dumps(data).encode('utf-8')
                except (json.JSONDecodeError, Exception) as e:
                    logger.error(f"Error processing response: {e}")

            return Response(content=content, status_code=resp.status, headers=response_headers)

    # --- TMDB merge full-fetch path ---
    else:
        logger.info("TMDB merge enabled, starting full data fetch...")
        all_items = []
        start_index = 0
        limit = 200

        new_params.pop("StartIndex", None)
        new_params.pop("Limit", None)

        while True:
            fetch_params = new_params.copy()
            fetch_params["StartIndex"] = str(start_index)
            fetch_params["Limit"] = str(limit)

            async with session.request(method, search_url, params=fetch_params, headers=headers_to_forward) as resp:
                if resp.status != 200:
                    logger.error(f"Batch fetch failed, status: {resp.status}")
                    return Response(
                        content=json.dumps({"Items": [], "TotalRecordCount": 0}).encode("utf-8"),
                        status_code=200,
                        media_type="application/json",
                    )
                batch_data = await resp.json()
                batch_items = batch_data.get("Items", [])
                if not batch_items:
                    break
                all_items.extend(batch_items)
                start_index += len(batch_items)
                if len(batch_items) < limit:
                    break

        logger.info(f"Full data fetch complete, total {len(all_items)} items.")

        merged_items = await handler_merger.merge_items_by_tmdb(all_items)
        if custom_sort_field and custom_sort_order:
            merged_items = _apply_custom_sort(merged_items, custom_sort_field, custom_sort_order)

        start_idx = int(client_start_index)
        limit_count = int(client_limit)
        return _paginate_and_respond(
            items=merged_items,
            start_idx=start_idx,
            limit_count=limit_count,
            vlib_id=found_vlib.id,
            page_ctx_key=page_ctx_key,
            page_ttl_seconds=page_ttl_seconds,
            cache_page=(method == "GET"),
        )

    return None
