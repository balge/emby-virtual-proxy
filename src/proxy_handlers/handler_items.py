# src/proxy_handlers/handler_items.py
"""
Virtual library items handler.

Architecture:
- Full item lists live in vlib_items_cache (populated by vlib_cache_manager).
- Browsing requests: read from cache → sort → slice → respond.
- Cache MISS: trigger on-demand fetch via vlib_cache_manager, then serve.
"""

import logging
import json
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
# Helpers
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
    if field == "DateLastMediaAdded":
        if item.get("Type") == "Series":
            val = _get_nested_value(item, "DateLastMediaAdded")
            return val if val else _get_nested_value(item, "DateCreated")
        return _get_nested_value(item, "DateCreated")
    return _get_nested_value(item, field)


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


def _apply_post_filter(
    items: List[Dict[str, Any]], post_filter_rules: List[Dict], match_all: bool = True
) -> List[Dict[str, Any]]:
    if not post_filter_rules:
        return items
    combiner = all if match_all else any
    return [
        item for item in items
        if combiner(
            _check_condition(_get_value_for_rule(item, rule.field), rule.operator, rule.value, rule.field)
            for rule in post_filter_rules
        )
    ]


def _apply_custom_sort(items: List[Dict[str, Any]], sort_field: str, sort_order: str) -> List[Dict[str, Any]]:
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
# Sort helpers
# ---------------------------------------------------------------------------

def _apply_client_sort(items: List[Dict[str, Any]], request: Request):
    """Apply client-requested sort from query params. Mutates in-place."""
    sort_by = request.query_params.get("SortBy", "SortName")
    sort_order = request.query_params.get("SortOrder", "Ascending")
    primary_sort = sort_by.split(",")[0] if sort_by else "SortName"
    reverse = sort_order.startswith("Descending")
    emby_field = SORT_FIELD_MAP.get(primary_sort, "SortName")
    items.sort(key=lambda x: (x.get(emby_field) or ""), reverse=reverse)


def _make_page_response(items: List[Dict[str, Any]], start_idx: int, limit_count: int) -> Response:
    """Slice items for the requested page and return Response."""
    total = len(items)
    page = items[start_idx: start_idx + limit_count]
    data = {"Items": page, "TotalRecordCount": total, "StartIndex": start_idx}
    return Response(content=json.dumps(data).encode("utf-8"), status_code=200, media_type="application/json")


# ---------------------------------------------------------------------------
# Data fetching helper (used by handler_latest for source-library scoping)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Random library handler
# ---------------------------------------------------------------------------

async def _fetch_user_genre_preferences(
    session: ClientSession, real_emby_url: str, user_id: str, headers: Dict
) -> List[str]:
    url = f"{real_emby_url}/emby/Users/{user_id}/Items"
    params = {
        "IsPlayed": "true", "SortBy": "DatePlayed", "SortOrder": "Descending",
        "Recursive": "true", "IncludeItemTypes": "Movie,Series",
        "Fields": "Genres", "Limit": "200",
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
        logger.warning(f"Failed to fetch user play history: {e}")
    return [g for g, _ in genre_counter.most_common(10)]


def _weighted_random_select(items: List[Dict], preferred_genres: List[str], count: int) -> List[Dict]:
    if not items:
        return []
    if not preferred_genres:
        return random_module.sample(items, min(count, len(items)))
    preferred_set = set(g.lower() for g in preferred_genres)
    weighted = [(item, 1 + sum(1 for g in [g.lower() for g in item.get("Genres", [])] if g in preferred_set) * 2) for item in items]
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


async def _handle_random_library(
    request: Request, found_vlib: VirtualLibrary,
    user_id: str, real_emby_url: str,
    session: ClientSession, config: AppConfig,
    start_idx: int, limit_count: int,
) -> Response:
    """Handle 'random' type virtual library."""
    from vlib_cache_manager import FETCH_FIELDS
    cache_key = f"random:{user_id}:{found_vlib.id}"

    cached = vlib_items_cache.get(cache_key)
    if cached:
        logger.info(f"Random '{found_vlib.name}': cache HIT for user {user_id}")
        return _make_page_response(cached, start_idx, limit_count)

    logger.info(f"Random '{found_vlib.name}': generating for user {user_id}")
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
            logger.error(f"Random: failed to fetch real libraries: {e}")
    if not source_libs:
        empty = {"Items": [], "TotalRecordCount": 0, "StartIndex": 0}
        return Response(content=json.dumps(empty).encode('utf-8'), status_code=200, media_type="application/json")

    preferred_genres = await _fetch_user_genre_preferences(session, real_emby_url, user_id, headers_to_forward)
    has_prefs = len(preferred_genres) > 0

    fetch_params = {
        "Recursive": "true", "IsPlayed": "false", "Fields": FETCH_FIELDS,
        "SortBy": "Random", "SortOrder": "Ascending",
    }
    token = request.query_params.get("X-Emby-Token")
    if token:
        fetch_params["X-Emby-Token"] = token

    all_movies: List[Dict] = []
    all_series: List[Dict] = []
    for pid in source_libs:
        for item_type, target in [("Movie", all_movies), ("Series", all_series)]:
            p = dict(fetch_params, ParentId=pid, IncludeItemTypes=item_type, Limit="300")
            try:
                async with session.get(search_url, params=p, headers=headers_to_forward) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        target.extend(data.get("Items", []))
            except Exception as e:
                logger.warning(f"Random fetch failed: {e}")

    # Deduplicate
    seen: set = set()
    for lst in [all_movies, all_series]:
        deduped = [item for item in lst if not (item.get("Id") in seen or seen.add(item.get("Id")))]
        lst.clear()
        lst.extend(deduped)

    target_per_type = 15
    if has_prefs:
        sel_m = _weighted_random_select(all_movies, preferred_genres, target_per_type)
        sel_s = _weighted_random_select(all_series, preferred_genres, target_per_type)
    else:
        sel_m = random_module.sample(all_movies, min(target_per_type, len(all_movies)))
        sel_s = random_module.sample(all_series, min(target_per_type, len(all_series)))

    # Auto-fill
    for sel, pool in [(sel_m, all_movies), (sel_s, all_series)]:
        if len(sel) < target_per_type:
            sel_ids = {it.get("Id") for it in sel}
            rem = [x for x in pool if x.get("Id") not in sel_ids]
            sel.extend(random_module.sample(rem, min(target_per_type - len(sel), len(rem))))

    if len(sel_m) + len(sel_s) < 30:
        all_sel_ids = {it.get("Id") for it in sel_m + sel_s}
        rem_all = [i for i in all_movies + all_series if i.get("Id") not in all_sel_ids]
        sel_m.extend(random_module.sample(rem_all, min(30 - len(sel_m) - len(sel_s), len(rem_all))))

    all_items = sel_m + sel_s
    random_module.shuffle(all_items)

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
            logger.info(f"ParentId {parent_id_from_param} matched vlib '{found_vlib.name}'")

    if not found_vlib and method == "GET" and 'Items' in full_path:
        path_parts = full_path.split('/')
        try:
            idx = path_parts.index('Items')
            if idx + 1 < len(path_parts):
                found_vlib = next((v for v in config.virtual_libraries if v.id == path_parts[idx + 1]), None)
        except ValueError:
            pass

    if not found_vlib:
        has_id_param = any(key.lower().endswith('id') for key in params.keys())
        if not has_id_param:
            return await handler_views.handle_view_injection(request, full_path, method, real_emby_url, session, config)
        return None

    logger.info(f"Intercepted vlib '{found_vlib.name}', type={found_vlib.resource_type}")

    user_id = params.get("UserId")
    if not user_id:
        parts = full_path.split('/')
        if 'Users' in parts:
            try:
                user_id = parts[parts.index('Users') + 1]
            except (ValueError, IndexError):
                pass
    if not user_id:
        return Response(content="UserId not found", status_code=400)

    if found_vlib.hidden:
        start_idx = int(params.get("StartIndex", 0))
        empty = {"Items": [], "TotalRecordCount": 0, "StartIndex": start_idx}
        return Response(content=json.dumps(empty).encode("utf-8"), status_code=200, media_type="application/json")

    # --- RSS ---
    if found_vlib.resource_type == "rsshub":
        rss_handler = RssHandler()
        response_data = await rss_handler.handle(
            request_path=full_path, vlib_id=found_vlib.id,
            request_params=params, user_id=user_id,
            session=session, real_emby_url=real_emby_url, request_headers=request.headers,
        )
        start_idx = int(params.get("StartIndex", 0))
        limit_str = params.get("Limit")
        final_items = response_data.get("Items", [])
        if limit_str:
            try:
                paginated = final_items[start_idx: start_idx + int(limit_str)]
            except (ValueError, TypeError):
                paginated = final_items[start_idx:]
        else:
            paginated = final_items[start_idx:]
        return Response(
            content=json.dumps({"Items": paginated, "TotalRecordCount": len(final_items)}).encode('utf-8'),
            media_type="application/json",
        )

    # --- Random ---
    if found_vlib.resource_type == "random":
        return await _handle_random_library(
            request, found_vlib, user_id, real_emby_url, session, config,
            int(params.get("StartIndex", 0)), int(params.get("Limit", 50)),
        )

    # --- Standard: serve from cache, on-demand fetch on miss ---
    start_idx = int(params.get("StartIndex", 0))
    limit_count = int(params.get("Limit", 50))

    cached_items = vlib_items_cache.get(found_vlib.id)
    if cached_items is not None:
        logger.info(f"Cache HIT '{found_vlib.name}': {len(cached_items)} items")
        items = list(cached_items)
        _apply_client_sort(items, request)
        return _make_page_response(items, start_idx, limit_count)

    # Cache MISS: fetch on demand via cache manager
    logger.info(f"Cache MISS '{found_vlib.name}', fetching from Emby...")
    from vlib_cache_manager import refresh_vlib_cache
    await refresh_vlib_cache(found_vlib, config)

    cached_items = vlib_items_cache.get(found_vlib.id)
    if cached_items is not None:
        items = list(cached_items)
        _apply_client_sort(items, request)
        return _make_page_response(items, start_idx, limit_count)

    # Still no data — return empty
    logger.warning(f"No data for '{found_vlib.name}' after fetch attempt")
    return _make_page_response([], start_idx, limit_count)
