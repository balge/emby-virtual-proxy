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
from typing import List, Any, Dict, Optional

from . import handler_merger, handler_views
from ._filter_translator import translate_rules
from .handler_rss import RssHandler
from proxy_cache import vlib_items_cache, random_recommend_cache
logger = logging.getLogger(__name__)

# Country code to possible name variants for ProductionLocations matching
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

# --- Post-filter logic ---
def _get_nested_value(item: Dict[str, Any], field_path: str) -> Any:
    keys = field_path.split('.')
    value = item
    for key in keys:
        if isinstance(value, dict): value = value.get(key)
        else: return None
    return value

def _check_condition(item_value: Any, operator: str, rule_value: str, field: str = "") -> bool:
    if operator not in ["is_empty", "is_not_empty"]:
        if item_value is None: return False
        if isinstance(item_value, list):
            # For ProductionLocations, expand country code to match multiple name variants
            if field == "ProductionLocations" and rule_value:
                code_upper = rule_value.upper()
                variants = COUNTRY_CODE_MAP.get(code_upper, [rule_value.lower()])
                if operator == "contains":
                    return any(
                        any(v in str(el).lower() for v in variants)
                        for el in item_value
                    )
                if operator == "not_contains":
                    return not any(
                        any(v in str(el).lower() for v in variants)
                        for el in item_value
                    )
                if operator == "equals":
                    return any(
                        any(str(el).lower() == v for v in variants)
                        for el in item_value
                    )
                if operator == "not_equals":
                    return not any(
                        any(str(el).lower() == v for v in variants)
                        for el in item_value
                    )
                return False
            # Generic list matching (case-insensitive substring)
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
                if operator == "greater_than": return float(item_value) > float(rule_value)
                if operator == "less_than": return float(item_value) < float(rule_value)
            except (ValueError, TypeError):
                # Fallback: string comparison for ISO datetime strings
                # Normalize both to comparable format (trim timezone suffix and microseconds)
                try:
                    iv = str(item_value).replace('Z', '').split('.')[0][:19]  # "YYYY-MM-DDTHH:MM:SS"
                    rv = str(rule_value).replace('Z', '').split('.')[0][:19]
                    if operator == "greater_than": return iv > rv
                    if operator == "less_than": return iv < rv
                except Exception:
                    return False
        item_value_str = str(item_value).lower()
        rule_value_str = str(rule_value).lower()
        if operator == "equals": return item_value_str == rule_value_str
        if operator == "not_equals": return item_value_str != rule_value_str
        if operator == "contains": return rule_value_str in item_value_str
        if operator == "not_contains": return rule_value_str not in item_value_str
    if operator == "is_empty": return item_value is None or item_value == '' or item_value == []
    if operator == "is_not_empty": return item_value is not None and item_value != '' and item_value != []
    return False

def _get_value_for_rule(item: Dict[str, Any], field: str) -> Any:
    """Get item value for a rule field, with smart logic for special fields."""
    # DateLastMediaAdded: Movie uses DateCreated, Series uses DateLastMediaAdded
    if field == "DateLastMediaAdded":
        item_type = item.get("Type", "")
        if item_type == "Series":
            # 优先使用 DateLastMediaAdded，这是 Emby 维护的"最后入库媒体"时间
            val = _get_nested_value(item, "DateLastMediaAdded")
            if val:
                return val
            # 回退到 DateCreated，但这只是 Series 条目本身的创建时间
            return _get_nested_value(item, "DateCreated")
        else:
            # Movie / Video / others: use DateCreated as "last added" time
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
    Stops early once items are older than threshold (SortBy=DateCreated desc).
    Returns (series_ids, series_latest_map).
    All source libraries are scanned concurrently (major latency win vs sequential).
    """
    page_limit = 400  # fewer round-trips than 200; payload still small (2 fields)

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
        scan_tasks = [_scan_one_parent(pid) for pid in source_parent_ids]
        parts = await asyncio.gather(*scan_tasks)
    else:
        parts = [await _scan_one_parent(None)]

    series_ids: set[str] = set()
    series_latest: Dict[str, datetime] = {}
    for local_ids, local_latest in parts:
        for sid in local_ids:
            series_ids.add(sid)
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
    """Fetch items recent by DateCreated; stop early once older than threshold. Libraries run in parallel."""
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

    seen: set[str] = set()
    merged: List[Dict] = []
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
    """Fetch by Ids in chunks; chunks run concurrently (bounded) to reduce wall time."""
    if not ids:
        return []

    chunks = [ids[i : i + chunk_size] for i in range(0, len(ids), chunk_size)]
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

def _sort_by_effective_last_media_added(
    items: List[Dict[str, Any]],
    series_latest_map: Dict[str, datetime],
    sort_order: str,
) -> List[Dict[str, Any]]:
    """
    Sort by effective last-media-added time:
    - Series: newest Episode.DateCreated from series_latest_map.
    - Others: DateCreated.
    """
    reverse = (sort_order == "desc" or str(sort_order).startswith("Desc"))

    def _k(it: Dict[str, Any]):
        if it.get("Type") == "Series":
            sid = str(it.get("Id") or "")
            dt = series_latest_map.get(sid)
            if dt is not None:
                return dt
            return _parse_iso_dt(it.get("DateLastMediaAdded")) or _parse_iso_dt(it.get("DateCreated")) or datetime.min.replace(tzinfo=timezone.utc)
        return _parse_iso_dt(it.get("DateCreated")) or datetime.min.replace(tzinfo=timezone.utc)

    items.sort(key=_k, reverse=reverse)
    return items

def _apply_post_filter(items: List[Dict[str, Any]], post_filter_rules: List[Dict], match_all: bool = True) -> List[Dict[str, Any]]:
    if not post_filter_rules: return items
    mode = "AND" if match_all else "OR"
    logger.info(f"Applying {len(post_filter_rules)} post-filter rules ({mode}) on {len(items)} items.")
    filtered_items = []
    combiner = all if match_all else any
    for item in items:
        if combiner(_check_condition(_get_value_for_rule(item, rule.field), rule.operator, rule.value, rule.field) for rule in post_filter_rules):
            filtered_items.append(item)
    return filtered_items


def _apply_custom_sort(items: List[Dict[str, Any]], sort_field: str, sort_order: str) -> List[Dict[str, Any]]:
    """
    对 items 按指定字段排序。
    sort_field: 前端传来的字段名（如 CommunityRating, PremiereDate 等）
    sort_order: "asc" 正序 | "desc" 倒序
    """
    if not items or not sort_field or not sort_order:
        return items

    reverse = (sort_order == "desc")

    def sort_key(item):
        val = _get_value_for_rule(item, sort_field)
        if val is None:
            # None 值排到最后
            return (1, "")
        # 尝试数值比较
        try:
            return (0, float(val))
        except (ValueError, TypeError):
            return (0, str(val))

    items.sort(key=sort_key, reverse=reverse)
    logger.info(f"Custom sort applied: field={sort_field}, order={sort_order}, items={len(items)}")
    return items


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


async def _fetch_items_for_parent_id(
    session: ClientSession, method: str, search_url: str,
    base_params: Dict, parent_id: str, headers: Dict
) -> List[Dict]:
    """Fetch all items from a single parent library (real Emby library) with pagination."""
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
    """
    Fetch items from a single parent library up to max_items.
    Returns (items, total_record_count_from_emby_or_None).

    This is a performance optimization for virtual libraries: most clients only need
    StartIndex+Limit items to render the current page, so we can avoid full-library scans.
    """
    if max_items <= 0:
        return [], None

    collected: List[Dict] = []
    start_index = 0
    limit = 400  # larger pages = fewer round-trips per source library
    total_record_count: int | None = None

    fetch_params = base_params.copy()
    fetch_params["ParentId"] = parent_id
    # We'll control paging here
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


async def _handle_source_library_scoped_request(
    request: Request, method: str, found_vlib: VirtualLibrary,
    new_params: Dict, user_id: str, real_emby_url: str,
    session: ClientSession, config: AppConfig,
    client_start_index: str, client_limit: str
) -> Response:
    """
    Handle virtual library requests scoped to specific real libraries.
    Issues parallel requests per source library, merges results, then applies
    post-filter / TMDB merge / pagination in the proxy.
    """
    logger.info(f"Source library scoping enabled for '{found_vlib.name}': {found_vlib.source_libraries}")

    headers_to_forward = _build_headers_to_forward(request)
    search_url = f"{real_emby_url}/emby/Users/{user_id}/Items"

    # Apply advanced filter translation
    post_filter_rules = []
    filter_match_all = True
    custom_sort_field = None
    custom_sort_order = None
    if found_vlib.advanced_filter_id:
        adv_filter = next((f for f in config.advanced_filters if f.id == found_vlib.advanced_filter_id), None)
        if adv_filter:
            emby_native_params, post_filter_rules = translate_rules(adv_filter.rules)
            new_params.update(emby_native_params)
            filter_match_all = adv_filter.match_all
            custom_sort_field = adv_filter.sort_field
            custom_sort_order = adv_filter.sort_order

    if new_params.get("IsMovie") == "true":
        new_params["IncludeItemTypes"] = "Movie"
    elif new_params.get("IsSeries") == "true":
        new_params["IncludeItemTypes"] = "Series"

    is_tmdb_merge_enabled = found_vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id

    # Optimized DateLastMediaAdded flow for scoped libraries:
    # derive updated Series from Episode.DateCreated and avoid broad post-filter scans.
    date_last_media_added_rule = None
    if post_filter_rules:
        dla_rules = [
            r for r in post_filter_rules
            if getattr(r, "field", None) == "DateLastMediaAdded"
            and getattr(r, "operator", None) == "greater_than"
            and getattr(r, "value", None)
        ]
        if len(dla_rules) == 1 and (filter_match_all or len(post_filter_rules) == 1):
            date_last_media_added_rule = dla_rules[0]

    if date_last_media_added_rule:
        threshold_dt = _parse_iso_dt(getattr(date_last_media_added_rule, "value", None))
        if threshold_dt:
            logger.info(
                f"Scoped optimized DateLastMediaAdded flow for '{found_vlib.name}', "
                f"threshold={threshold_dt.isoformat()}"
            )

            scan_base_params = dict(new_params)
            scan_base_params.pop("StartIndex", None)
            scan_base_params.pop("Limit", None)
            scan_base_params.pop("SortBy", None)
            scan_base_params.pop("SortOrder", None)

            ep_task = _fetch_recent_episodes_series_ids(
                session=session,
                search_url=search_url,
                headers=headers_to_forward,
                base_params=scan_base_params,
                threshold_dt=threshold_dt,
                source_parent_ids=list(found_vlib.source_libraries),
            )
            mv_task = _fetch_items_recent_by_datecreated(
                session=session,
                search_url=search_url,
                headers=headers_to_forward,
                base_params=scan_base_params,
                threshold_dt=threshold_dt,
                include_item_types="Movie,Video",
                source_parent_ids=list(found_vlib.source_libraries),
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

            remaining_post_filters = [r for r in post_filter_rules if getattr(r, "field", None) != "DateLastMediaAdded"]
            if remaining_post_filters:
                series_items = _apply_post_filter(series_items, remaining_post_filters, filter_match_all)
                movie_video_items = _apply_post_filter(movie_video_items, remaining_post_filters, filter_match_all)

            all_items = series_items + movie_video_items

            if is_tmdb_merge_enabled:
                all_items = await handler_merger.merge_items_by_tmdb(all_items)

            seen_ids = set()
            deduped_items = []
            for item in all_items:
                item_id = item.get("Id")
                if item_id and item_id not in seen_ids:
                    seen_ids.add(item_id)
                    deduped_items.append(item)
                elif not item_id:
                    deduped_items.append(item)
            all_items = deduped_items

            sort_by = request.query_params.get("SortBy", "DateCreated")
            sort_order = request.query_params.get("SortOrder", "Descending")
            reverse = sort_order.startswith("Descending")
            primary_sort = sort_by.split(",")[0] if sort_by else "DateCreated"

            if primary_sort in ("DateCreated", "DateLastMediaAdded"):
                if primary_sort == "DateLastMediaAdded":
                    _sort_by_effective_last_media_added(all_items, series_latest_map, sort_order)
                else:
                    def _k(it: Dict[str, Any]):
                        if it.get("Type") == "Series":
                            sid = str(it.get("Id") or "")
                            dt = series_latest_map.get(sid)
                            return dt or _parse_iso_dt(it.get("DateCreated")) or datetime.min.replace(tzinfo=timezone.utc)
                        return _parse_iso_dt(it.get("DateCreated")) or datetime.min.replace(tzinfo=timezone.utc)
                    all_items.sort(key=_k, reverse=reverse)
            else:
                sort_field_map = {
                    "SortName": "SortName", "DateCreated": "DateCreated",
                    "PremiereDate": "PremiereDate", "ProductionYear": "ProductionYear",
                    "CommunityRating": "CommunityRating", "DatePlayed": "DatePlayed",
                }
                emby_field = sort_field_map.get(primary_sort, "SortName")
                all_items.sort(key=lambda x: (x.get(emby_field) or ""), reverse=reverse)

            if custom_sort_field and custom_sort_order:
                if custom_sort_field == "DateLastMediaAdded":
                    _sort_by_effective_last_media_added(all_items, series_latest_map, custom_sort_order)
                else:
                    all_items = _apply_custom_sort(all_items, custom_sort_field, custom_sort_order)

            total_record_count = len(all_items)
            start_idx = int(client_start_index)
            limit_count = int(client_limit)
            paginated_items = all_items[start_idx : start_idx + limit_count]

            if paginated_items:
                vlib_items_cache[found_vlib.id] = paginated_items

            final_data = {
                "Items": paginated_items,
                "TotalRecordCount": total_record_count,
                "StartIndex": start_idx
            }
            logger.info(
                f"Scoped optimized DateLastMediaAdded result: total={total_record_count}, page={len(paginated_items)}"
            )
            content = json.dumps(final_data).encode('utf-8')
            return Response(content=content, status_code=200, media_type="application/json")

    # Performance: if client doesn't need TotalRecordCount, avoid full scans.
    enable_total = request.query_params.get("EnableTotalRecordCount", "true").lower() != "false"

    start_idx = int(client_start_index)
    limit_count = int(client_limit)

    async def _fetch_merge_sort_paginate(per_lib_target: int) -> tuple[list[dict], int]:
        # Parallel fetch from all source libraries (bounded)
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

        # Deduplicate by Emby Item Id (same item may appear in multiple libraries)
        seen_ids = set()
        deduped_items = []
        for item in fetched:
            item_id = item.get("Id")
            if item_id and item_id not in seen_ids:
                seen_ids.add(item_id)
                deduped_items.append(item)
            elif not item_id:
                deduped_items.append(item)
        merged = deduped_items

        # Apply post-filter
        if post_filter_rules:
            merged = _apply_post_filter(merged, post_filter_rules, filter_match_all)

        # Apply TMDB merge
        if is_tmdb_merge_enabled:
            merged = await handler_merger.merge_items_by_tmdb(merged)

        # Sort: respect client SortBy/SortOrder if present
        sort_by = request.query_params.get("SortBy", "SortName")
        sort_order = request.query_params.get("SortOrder", "Ascending")
        sort_field_map = {
            "SortName": "SortName", "DateCreated": "DateCreated",
            "PremiereDate": "PremiereDate", "ProductionYear": "ProductionYear",
            "CommunityRating": "CommunityRating", "DatePlayed": "DatePlayed",
        }
        primary_sort = sort_by.split(",")[0] if sort_by else "SortName"
        emby_field = sort_field_map.get(primary_sort, "SortName")
        reverse = sort_order.startswith("Descending")
        merged.sort(key=lambda x: (x.get(emby_field) or ""), reverse=reverse)

        # Apply custom sort from advanced filter (overrides client sort)
        if custom_sort_field and custom_sort_order:
            merged = _apply_custom_sort(merged, custom_sort_field, custom_sort_order)

        # Paginate
        page = merged[start_idx : start_idx + limit_count]
        total_rc = summed_totals_local if enable_total else 0
        return page, total_rc

    # Fetch enough items to assemble the requested page after merge/dedupe/sort.
    buffer = max(50, limit_count)
    per_lib_target = start_idx + limit_count + buffer
    paginated_items, total_record_count = await _fetch_merge_sort_paginate(per_lib_target)

    # If we under-filled the page (due to dedupe/post-filter/merge), retry once with larger target.
    if len(paginated_items) < limit_count and per_lib_target < (start_idx + limit_count + buffer) * 4:
        retry_target = per_lib_target * 2
        logger.info(
            f"Scoped result under-filled (page={len(paginated_items)}/{limit_count}), retrying with per_lib_target={retry_target}."
        )
        paginated_items, total_record_count = await _fetch_merge_sort_paginate(retry_target)

    if paginated_items:
        vlib_items_cache[found_vlib.id] = paginated_items

    final_data = {
        "Items": paginated_items,
        "TotalRecordCount": total_record_count,
        "StartIndex": start_idx
    }
    logger.info(f"Source library scoped result: total={total_record_count}, page={len(paginated_items)}")

    content = json.dumps(final_data).encode('utf-8')
    return Response(content=content, status_code=200, media_type="application/json")


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
    # Return genres sorted by frequency
    return [g for g, _ in genre_counter.most_common(10)]


def _weighted_random_select(items: List[Dict], preferred_genres: List[str], count: int) -> List[Dict]:
    """Select items with preference weighting. Items matching user's genres get higher weight."""
    if not items:
        return []
    if not preferred_genres:
        return random_module.sample(items, min(count, len(items)))

    preferred_set = set(g.lower() for g in preferred_genres)
    weighted_items = []
    for item in items:
        item_genres = [g.lower() for g in item.get("Genres", [])]
        # More matching genres = higher weight
        match_count = sum(1 for g in item_genres if g in preferred_set)
        weight = 1 + match_count * 2  # base weight 1, +2 per matching genre
        weighted_items.append((item, weight))

    # Weighted random sampling without replacement
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
    client_start_index: str, client_limit: str
) -> Response:
    """
    Handle 'random' type virtual library.
    - Only fetches from configured source_libraries (required for random type)
    - Uses user's play history for weighted genre-based recommendations
    - Falls back to pure random if no play history
    - Auto-fills to 30 items (15+15) from random pool if preference-based selection is insufficient
    - Cached per user+vlib for 12 hours
    """
    cache_key = f"random:{user_id}:{found_vlib.id}"
    cached = random_recommend_cache.get(cache_key)
    if cached:
        logger.info(f"Random library '{found_vlib.name}': serving cached result for user {user_id}")
        all_items = cached
    else:
        logger.info(f"Random library '{found_vlib.name}': generating new recommendations for user {user_id}")
        headers_to_forward = _build_headers_to_forward(request)
        search_url = f"{real_emby_url}/emby/Users/{user_id}/Items"

        # 1. Source libraries are required for random type; filter out ignored
        ignore_set = set(config.ignore_libraries) if config.ignore_libraries else set()
        source_libs = list(found_vlib.source_libraries) if found_vlib.source_libraries else []
        if source_libs and ignore_set:
            source_libs = [lid for lid in source_libs if lid not in ignore_set]

        if not source_libs:
            # No source libraries configured: fetch from all real libraries minus ignored
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

        # 2. Fetch user's genre preferences from play history
        preferred_genres = await _fetch_user_genre_preferences(
            session, real_emby_url, user_id, headers_to_forward
        )
        has_preferences = len(preferred_genres) > 0
        if has_preferences:
            logger.info(f"User {user_id} preferred genres: {preferred_genres[:5]}")
        else:
            logger.info(f"User {user_id} has no play history, will use pure random selection.")

        # 3. Fetch candidate items from source libraries (unplayed only)
        fetch_params = {
            "Recursive": "true",
            "IsPlayed": "false",
            "Fields": "Genres,ProviderIds,ProductionYear,CommunityRating,DateCreated,ImageTags",
            "SortBy": "Random",
            "SortOrder": "Ascending",
        }
        # Inherit auth token
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

        # 4. Selection logic
        target_per_type = 15

        if has_preferences:
            # Weighted selection based on genre preferences
            selected_movies = _weighted_random_select(all_movies, preferred_genres, target_per_type)
            selected_series = _weighted_random_select(all_series, preferred_genres, target_per_type)
        else:
            # No play history: pure random
            selected_movies = random_module.sample(all_movies, min(target_per_type, len(all_movies)))
            selected_series = random_module.sample(all_series, min(target_per_type, len(all_series)))

        # 5. Auto-fill if insufficient: pick random from remaining pool
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

        # If one type is still short, fill from the other type's pool
        total_selected = len(selected_movies) + len(selected_series)
        if total_selected < 30:
            all_selected_ids = {item.get("Id") for item in selected_movies + selected_series}
            remaining_all = [i for i in all_movies + all_series if i.get("Id") not in all_selected_ids]
            fill_count = 30 - total_selected
            extra = random_module.sample(remaining_all, min(fill_count, len(remaining_all)))
            selected_movies.extend(extra)  # just append to combined list

        all_items = selected_movies + selected_series
        random_module.shuffle(all_items)

        logger.info(f"Random library '{found_vlib.name}': selected {len(selected_movies)} movies + {len(selected_series)} series for user {user_id}")

        # Cache the result
        random_recommend_cache[cache_key] = all_items

        if all_items:
            vlib_items_cache[found_vlib.id] = all_items

    # Paginate
    total = len(all_items)
    start_idx = int(client_start_index)
    limit_count = int(client_limit)
    paginated = all_items[start_idx : start_idx + limit_count]

    final_data = {"Items": paginated, "TotalRecordCount": total, "StartIndex": start_idx}
    return Response(content=json.dumps(final_data).encode('utf-8'), status_code=200, media_type="application/json")


async def handle_virtual_library_items(
    request: Request,
    full_path: str,
    method: str,
    real_emby_url: str,
    session: ClientSession,
    config: AppConfig
) -> Response | None:
    if "/Items/Prefixes" in full_path or "/Items/Counts" in full_path or "/Items/Latest" in full_path:
        return None

    params = request.query_params
    found_vlib = None

    parent_id_from_param = params.get("ParentId")
    if parent_id_from_param:
        found_vlib = next((vlib for vlib in config.virtual_libraries if vlib.id == parent_id_from_param), None)

    if not found_vlib and method == "GET" and 'Items' in full_path:
        path_parts = full_path.split('/')
        try:
            items_index = path_parts.index('Items')
            if items_index + 1 < len(path_parts):
                potential_path_vlib_id = path_parts[items_index + 1]
                found_vlib = next((vlib for vlib in config.virtual_libraries if vlib.id == potential_path_vlib_id), None)
        except ValueError: pass

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
                if user_id_index < len(path_parts_for_user): user_id = path_parts_for_user[user_id_index]
            except (ValueError, IndexError): pass
    if not user_id: return Response(content="UserId not found", status_code=400)

    if found_vlib.hidden:
        logger.info(f"Virtual library '{found_vlib.name}' is hidden; returning empty items.")
        start_idx = int(params.get("StartIndex", 0))
        empty = {"Items": [], "TotalRecordCount": 0, "StartIndex": start_idx}
        return Response(content=json.dumps(empty).encode("utf-8"), status_code=200, media_type="application/json")

    # --- Build request params ---
    new_params = {}

    client_start_index = params.get("StartIndex", "0")
    client_limit = params.get("Limit", "50")
    safe_params_to_inherit = [
        "SortBy", "SortOrder", "Fields", "EnableImageTypes", "ImageTypeLimit",
        "EnableTotalRecordCount", "X-Emby-Token", "StartIndex", "Limit"
    ]
    for key in safe_params_to_inherit:
        if key in params: new_params[key] = params[key]

    required_fields = ["ProviderIds", "Genres", "Tags", "Studios", "People", "OfficialRatings", "CommunityRating", "ProductionYear", "VideoRange", "Container", "ProductionLocations", "DateLastMediaAdded", "DateCreated"]
    if "Fields" in new_params:
        existing_fields = set(new_params["Fields"].split(','))
        missing_fields = [f for f in required_fields if f not in existing_fields]
        if missing_fields: new_params["Fields"] += "," + ",".join(missing_fields)
    else: new_params["Fields"] = ",".join(required_fields)

    new_params["Recursive"] = "true"
    new_params["IncludeItemTypes"] = "Movie,Series,Video"

    resource_map = {"collection": "CollectionIds", "tag": "TagIds", "person": "PersonIds", "genre": "GenreIds", "studio": "StudioIds"}
    if found_vlib.resource_type in resource_map:
        new_params[resource_map[found_vlib.resource_type]] = found_vlib.resource_id
    elif found_vlib.resource_type == "rsshub":
        # Delegate all RSS logic to RssHandler
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
                paginated_items = final_items[start_idx : start_idx + limit]
            except (ValueError, TypeError):
                paginated_items = final_items[start_idx:]
        else:
            paginated_items = final_items[start_idx:]

        final_response = {"Items": paginated_items, "TotalRecordCount": len(final_items)}
        return Response(content=json.dumps(final_response).encode('utf-8'), media_type="application/json")

    # --- Random type: delegate to dedicated handler ---
    if found_vlib.resource_type == "random":
        return await _handle_random_library(
            request, method, found_vlib, new_params, user_id,
            real_emby_url, session, config, client_start_index, client_limit
        )

    # --- Source library scoping: if configured, use multi-library merge branch ---
    # Also handles ignore_libraries: filter out ignored libs from source_libraries,
    # and for "all" type, auto-scope to all real libs minus ignored ones.
    ignore_set = set(config.ignore_libraries) if config.ignore_libraries else set()

    effective_source_libs = list(found_vlib.source_libraries) if found_vlib.source_libraries else []
    if effective_source_libs and ignore_set:
        effective_source_libs = [lid for lid in effective_source_libs if lid not in ignore_set]

    if found_vlib.resource_type == "all" and ignore_set and not effective_source_libs:
        # "all" type with ignored libraries: we need to fetch the real library list
        # and exclude ignored ones, then use scoped request
        from admin_server import get_real_libraries_hybrid_mode
        try:
            real_libs = await get_real_libraries_hybrid_mode()
            effective_source_libs = [lib["Id"] for lib in real_libs if lib["Id"] not in ignore_set]
        except Exception as e:
            logger.error(f"Failed to fetch real libraries for ignore filtering: {e}")

    if effective_source_libs and found_vlib.resource_type != "rsshub":
        # Temporarily set source_libraries on the vlib for the scoped handler
        found_vlib_copy = found_vlib.model_copy(update={"source_libraries": effective_source_libs})
        return await _handle_source_library_scoped_request(
            request, method, found_vlib_copy, new_params, user_id,
            real_emby_url, session, config, client_start_index, client_limit
        )

    # --- Advanced filter translation ---
    post_filter_rules = []
    filter_match_all = True
    custom_sort_field = None
    custom_sort_order = None
    date_last_media_added_rule = None
    if found_vlib.advanced_filter_id:
        adv_filter = next((f for f in config.advanced_filters if f.id == found_vlib.advanced_filter_id), None)
        if adv_filter:
            logger.info(f"Translating advanced filter '{adv_filter.name}' rules...")
            emby_native_params, post_filter_rules = translate_rules(adv_filter.rules)
            new_params.update(emby_native_params)
            filter_match_all = adv_filter.match_all
            custom_sort_field = adv_filter.sort_field
            custom_sort_order = adv_filter.sort_order
            if post_filter_rules: logger.info(f"{len(post_filter_rules)} rules require proxy-side post-filtering.")
        else:
            logger.warning(f"Virtual library references advanced filter ID '{found_vlib.advanced_filter_id}', but it was not found.")

    if post_filter_rules:
        dla_rules = [
            r for r in post_filter_rules
            if getattr(r, "field", None) == "DateLastMediaAdded"
            and getattr(r, "operator", None) == "greater_than"
            and getattr(r, "value", None)
        ]
        # Only enable optimized path when DateLastMediaAdded is the only post-filter,
        # or when match_all is True (AND semantics) so we can still apply other post-filters after.
        if len(dla_rules) == 1 and (filter_match_all or len(post_filter_rules) == 1):
            date_last_media_added_rule = dla_rules[0]

    if new_params.get("IsMovie") == "true":
        new_params["IncludeItemTypes"] = "Movie"
    elif new_params.get("IsSeries") == "true":
        new_params["IncludeItemTypes"] = "Series"

    is_tmdb_merge_enabled = found_vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id

    target_emby_api_path = f"Users/{user_id}/Items"
    search_url = f"{real_emby_url}/emby/{target_emby_api_path}"

    headers_to_forward = _build_headers_to_forward(request)

    logger.debug(f"Final optimized request to Emby: URL={search_url}, Params={new_params}")

    # --- Optimized DateLastMediaAdded flow (derive from Episode.DateCreated) ---
    if date_last_media_added_rule:
        threshold_dt = _parse_iso_dt(getattr(date_last_media_added_rule, "value", None))
        if threshold_dt:
            logger.info(f"Optimized DateLastMediaAdded: threshold={threshold_dt.isoformat()}")

            # Scope to source libraries if configured (respect ignore list)
            ignore_set = set(config.ignore_libraries) if config.ignore_libraries else set()
            source_parent_ids = list(found_vlib.source_libraries) if found_vlib.source_libraries else None
            if source_parent_ids and ignore_set:
                source_parent_ids = [lid for lid in source_parent_ids if lid not in ignore_set]
            if source_parent_ids and len(source_parent_ids) == 0:
                source_parent_ids = None

            scan_base_params = dict(new_params)
            # Remove client pagination; scans are self-paged
            scan_base_params.pop("StartIndex", None)
            scan_base_params.pop("Limit", None)
            # Remove client sort; we enforce DateCreated desc in scan
            scan_base_params.pop("SortBy", None)
            scan_base_params.pop("SortOrder", None)

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

            remaining_post_filters = [r for r in post_filter_rules if getattr(r, "field", None) != "DateLastMediaAdded"]
            if remaining_post_filters:
                series_items = _apply_post_filter(series_items, remaining_post_filters, filter_match_all)
                movie_video_items = _apply_post_filter(movie_video_items, remaining_post_filters, filter_match_all)

            combined = series_items + movie_video_items
            if is_tmdb_merge_enabled:
                combined = await handler_merger.merge_items_by_tmdb(combined)

            # Deduplicate by Id
            seen_ids = set()
            deduped = []
            for item in combined:
                iid = item.get("Id")
                if iid and iid in seen_ids:
                    continue
                if iid:
                    seen_ids.add(iid)
                deduped.append(item)

            # Sorting: if client sorts by DateCreated, use derived series latest episode date.
            sort_by = request.query_params.get("SortBy", "DateCreated")
            sort_order = request.query_params.get("SortOrder", "Descending")
            reverse = sort_order.startswith("Descending")
            primary_sort = sort_by.split(",")[0] if sort_by else "DateCreated"

            if primary_sort in ("DateCreated", "DateLastMediaAdded"):
                if primary_sort == "DateLastMediaAdded":
                    _sort_by_effective_last_media_added(deduped, series_latest_map, sort_order)
                else:
                    def _k(it: Dict[str, Any]):
                        if it.get("Type") == "Series":
                            sid = str(it.get("Id") or "")
                            dt = series_latest_map.get(sid)
                            return dt or _parse_iso_dt(it.get("DateCreated")) or datetime.min.replace(tzinfo=timezone.utc)
                        return _parse_iso_dt(it.get("DateCreated")) or datetime.min.replace(tzinfo=timezone.utc)
                    deduped.sort(key=_k, reverse=reverse)
            else:
                sort_field_map = {
                    "SortName": "SortName", "DateCreated": "DateCreated",
                    "PremiereDate": "PremiereDate", "ProductionYear": "ProductionYear",
                    "CommunityRating": "CommunityRating", "DatePlayed": "DatePlayed",
                }
                emby_field = sort_field_map.get(primary_sort, "SortName")
                deduped.sort(key=lambda x: (x.get(emby_field) or ""), reverse=reverse)

            if custom_sort_field and custom_sort_order:
                if custom_sort_field == "DateLastMediaAdded":
                    _sort_by_effective_last_media_added(deduped, series_latest_map, custom_sort_order)
                else:
                    deduped = _apply_custom_sort(deduped, custom_sort_field, custom_sort_order)

            total_record_count = len(deduped)
            start_idx = int(client_start_index)
            limit_count = int(client_limit)
            page_items = deduped[start_idx: start_idx + limit_count]
            final_data = {"Items": page_items, "TotalRecordCount": total_record_count, "StartIndex": start_idx}
            if page_items:
                vlib_items_cache[found_vlib.id] = page_items
            return Response(content=json.dumps(final_data).encode("utf-8"), status_code=200, media_type="application/json")

    # Standard pagination path (no TMDB merge, or has post-filter rules)
    if not is_tmdb_merge_enabled or post_filter_rules:
        if is_tmdb_merge_enabled and post_filter_rules:
            logger.warning("TMDB merge enabled but post-filter rules exist; merge will be partial (current page only).")

        async with session.request(method, search_url, params=new_params, headers=headers_to_forward) as resp:
            if resp.status != 200:
                content = await resp.read()
                return Response(content=content, status_code=resp.status, headers=resp.headers)

            content = await resp.read()
            response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ('transfer-encoding', 'connection', 'content-encoding', 'content-length')}

            if "application/json" in resp.headers.get("Content-Type", ""):
                try:
                    data = json.loads(content)
                    items_list = data.get("Items", [])

                    if post_filter_rules:
                        items_list = _apply_post_filter(items_list, post_filter_rules, filter_match_all)

                    if is_tmdb_merge_enabled:
                        items_list = await handler_merger.merge_items_by_tmdb(items_list)

                    # Apply custom sort from advanced filter
                    if custom_sort_field and custom_sort_order:
                        items_list = _apply_custom_sort(items_list, custom_sort_field, custom_sort_order)

                    data["Items"] = items_list
                    logger.info(f"Native filter/merge done. Emby total: {data.get('TotalRecordCount')}, page items: {len(items_list)}")

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
                    return Response(content=json.dumps({"Items": [], "TotalRecordCount": 0}), status_code=200, media_type="application/json")

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

        # Apply custom sort from advanced filter
        if custom_sort_field and custom_sort_order:
            merged_items = _apply_custom_sort(merged_items, custom_sort_field, custom_sort_order)

        total_record_count = len(merged_items)
        start_idx = int(client_start_index)
        limit_count = int(client_limit)
        paginated_items = merged_items[start_idx : start_idx + limit_count]

        final_data = {
            "Items": paginated_items,
            "TotalRecordCount": total_record_count,
            "StartIndex": start_idx
        }

        if paginated_items:
            vlib_items_cache[found_vlib.id] = paginated_items

        content = json.dumps(final_data).encode('utf-8')
        response_headers = {
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': str(len(content))
        }
        return Response(content=content, status_code=200, headers=response_headers)

    return None
