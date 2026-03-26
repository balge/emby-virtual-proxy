# src/proxy_handlers/handler_items.py

import logging
import json
import asyncio
from fastapi import Request, Response
from aiohttp import ClientSession
from models import AppConfig, AdvancedFilter, VirtualLibrary
from typing import List, Any, Dict, Optional

from . import handler_merger, handler_views
from ._filter_translator import translate_rules
from .handler_rss import RssHandler
from proxy_cache import vlib_items_cache
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
                # Fallback: string comparison works for ISO datetime strings
                try:
                    iv = str(item_value)[:19]  # Trim to "YYYY-MM-DDTHH:MM:SS"
                    rv = str(rule_value)[:19]
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
            return _get_nested_value(item, "DateLastMediaAdded") or _get_nested_value(item, "DateCreated")
        else:
            # Movie / Video / others: use DateCreated as "last added" time
            return _get_nested_value(item, "DateCreated")
    return _get_nested_value(item, field)

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
    if found_vlib.advanced_filter_id:
        adv_filter = next((f for f in config.advanced_filters if f.id == found_vlib.advanced_filter_id), None)
        if adv_filter:
            emby_native_params, post_filter_rules = translate_rules(adv_filter.rules)
            new_params.update(emby_native_params)
            filter_match_all = adv_filter.match_all

    if new_params.get("IsMovie") == "true":
        new_params["IncludeItemTypes"] = "Movie"
    elif new_params.get("IsSeries") == "true":
        new_params["IncludeItemTypes"] = "Series"

    is_tmdb_merge_enabled = found_vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id

    # Parallel fetch from all source libraries
    tasks = [
        _fetch_items_for_parent_id(session, method, search_url, new_params, pid, headers_to_forward)
        for pid in found_vlib.source_libraries
    ]
    results = await asyncio.gather(*tasks)
    all_items = []
    for items in results:
        all_items.extend(items)

    logger.info(f"Source library scoped fetch complete: {len(all_items)} total items from {len(found_vlib.source_libraries)} libraries.")

    # Deduplicate by Emby Item Id (same item may appear in multiple libraries)
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

    # Apply post-filter
    if post_filter_rules:
        all_items = _apply_post_filter(all_items, post_filter_rules, filter_match_all)

    # Apply TMDB merge
    if is_tmdb_merge_enabled:
        all_items = await handler_merger.merge_items_by_tmdb(all_items)

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
    all_items.sort(key=lambda x: (x.get(emby_field) or ""), reverse=reverse)

    # Paginate
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
    logger.info(f"Source library scoped result: total={total_record_count}, page={len(paginated_items)}")

    content = json.dumps(final_data).encode('utf-8')
    return Response(content=content, status_code=200, media_type="application/json")


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
    if found_vlib.advanced_filter_id:
        adv_filter = next((f for f in config.advanced_filters if f.id == found_vlib.advanced_filter_id), None)
        if adv_filter:
            logger.info(f"Translating advanced filter '{adv_filter.name}' rules...")
            emby_native_params, post_filter_rules = translate_rules(adv_filter.rules)
            new_params.update(emby_native_params)
            filter_match_all = adv_filter.match_all
            if post_filter_rules: logger.info(f"{len(post_filter_rules)} rules require proxy-side post-filtering.")
        else:
            logger.warning(f"Virtual library references advanced filter ID '{found_vlib.advanced_filter_id}', but it was not found.")

    if new_params.get("IsMovie") == "true":
        new_params["IncludeItemTypes"] = "Movie"
    elif new_params.get("IsSeries") == "true":
        new_params["IncludeItemTypes"] = "Series"

    is_tmdb_merge_enabled = found_vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id

    target_emby_api_path = f"Users/{user_id}/Items"
    search_url = f"{real_emby_url}/emby/{target_emby_api_path}"

    headers_to_forward = _build_headers_to_forward(request)

    logger.debug(f"Final optimized request to Emby: URL={search_url}, Params={new_params}")

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
