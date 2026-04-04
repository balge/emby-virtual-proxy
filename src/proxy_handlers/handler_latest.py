# src/proxy_handlers/handler_latest.py

import logging
import json
from fastapi import Request, Response
from aiohttp import ClientSession
from models import AppConfig
from typing import List, Dict
import asyncio
from pathlib import Path

from . import handler_merger
from . import handler_autogen
from ._filter_translator import translate_rules
from .handler_items import (
    _apply_post_filter,
    _apply_custom_sort,
    _build_headers_to_forward,
    _fetch_all_items_for_parent,
    _deduplicate_by_id,
)
from emby_api_client import get_real_libraries_hybrid_mode

logger = logging.getLogger(__name__)

COVERS_DIR = Path("/app/config/images")

async def handle_home_latest_items(
    request: Request,
    full_path: str,
    method: str,
    real_emby_url: str,
    session: ClientSession,
    config: AppConfig
) -> Response | None:
    if "/Items/Latest" not in full_path or method != "GET":
        return None

    params = request.query_params
    parent_id = params.get("ParentId")
    if not parent_id: return None

    found_vlib = next((vlib for vlib in config.virtual_libraries if vlib.id == parent_id), None)
    if not found_vlib:
        return None

    user_id = params.get("UserId")
    if not user_id:
        path_parts_for_user = full_path.split('/')
        if 'Users' in path_parts_for_user:
            try:
                user_id_index = path_parts_for_user.index('Users') + 1
                if user_id_index < len(path_parts_for_user): user_id = path_parts_for_user[user_id_index]
            except (ValueError, IndexError): pass
    if not user_id: return None

    if found_vlib.hidden:
        logger.info(f"HOME_LATEST: Virtual library '{found_vlib.name}' is hidden; returning empty latest.")
        return Response(content=json.dumps([]).encode("utf-8"), status_code=200, headers={"Content-Type": "application/json"})

    # Random library branch: reuse cached random items for latest
    if found_vlib.resource_type == 'random':
        from proxy_cache import vlib_items_cache
        cached = vlib_items_cache.get_for_user(user_id, found_vlib.id)
        if cached is not None:
            limit = int(params.get("Limit", 20))
            items = cached[:limit]
        else:
            items = []
        content = json.dumps(items).encode('utf-8')
        return Response(content=content, status_code=200, headers={"Content-Type": "application/json"})

    # RSS library branch
    if found_vlib.resource_type == 'rsshub':
        logger.info(f"HOME_LATEST_HANDLER: Intercepting request for latest items in RSS vlib '{found_vlib.name}'.")

        from .handler_rss import RssHandler
        rss_handler = RssHandler()
        limit = int(request.query_params.get("Limit", 20))

        latest_items_from_db = rss_handler.rss_library_db.fetchall(
            "SELECT tmdb_id, media_type, emby_item_id FROM rss_library_items WHERE library_id = ? ORDER BY rowid DESC LIMIT ?",
            (found_vlib.id, limit)
        )
        if not latest_items_from_db:
            return Response(content=json.dumps([]).encode('utf-8'), status_code=200, headers={"Content-Type": "application/json"})

        existing_emby_ids = [str(item['emby_item_id']) for item in latest_items_from_db if item['emby_item_id']]
        missing_items_info = [{'tmdb_id': item['tmdb_id'], 'media_type': item['media_type']} for item in latest_items_from_db if not item['emby_item_id']]

        existing_items_data = []
        server_id = None
        if existing_emby_ids:
            existing_items_data = await rss_handler._get_emby_items_by_ids_async(
                existing_emby_ids,
                request_params=request.query_params,
                user_id=user_id,
                session=session,
                real_emby_url=real_emby_url,
                request_headers=request.headers
            )
            if existing_items_data:
                server_id = existing_items_data[0].get("ServerId")

        if not server_id:
            server_id = rss_handler.config.emby_server_id or "emby"

        # 只返回 Emby 中实际存在的项目，不展示占位符
        # missing_items_placeholders = []
        # for item_info in missing_items_info:
        #     item = rss_handler._get_item_from_tmdb(item_info['tmdb_id'], item_info['media_type'], server_id)
        #     if item:
        #         missing_items_placeholders.append(item)
        # final_items = existing_items_data + missing_items_placeholders
        final_items = existing_items_data

        content = json.dumps(final_items).encode('utf-8')
        return Response(content=content, status_code=200, headers={"Content-Type": "application/json"})

    logger.info(f"HOME_LATEST_HANDLER: Intercepting request for latest items in vlib '{found_vlib.name}'.")

    # Auto-generate cover if missing
    image_file = COVERS_DIR / f"{found_vlib.id}.jpg"
    if not image_file.is_file():
        logger.info(f"HOME_LATEST: Cover missing for vlib '{found_vlib.name}' ({found_vlib.id}), triggering auto-generation.")
        if found_vlib.id not in handler_autogen.GENERATION_IN_PROGRESS:
            api_key = params.get("X-Emby-Token") or config.emby_api_key
            if user_id and api_key:
                asyncio.create_task(handler_autogen.generate_poster_in_background(found_vlib.id, user_id, api_key))

    new_params = {}
    safe_params_to_inherit = ["Fields", "IncludeItemTypes", "EnableImageTypes", "ImageTypeLimit", "X-Emby-Token", "EnableUserData", "Limit", "ParentId"]
    for key in safe_params_to_inherit:
        if key in params: new_params[key] = params[key]

    new_params["SortBy"] = "DateCreated"
    new_params["SortOrder"] = "Descending"
    new_params["Recursive"] = "true"
    new_params["IncludeItemTypes"] = "Movie,Series,Video"

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

    is_tmdb_merge_enabled = found_vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id
    if post_filter_rules or is_tmdb_merge_enabled:
        fetch_limit = 200
        client_limit = int(params.get("Limit", 20))
        try: fetch_limit = min(max(client_limit * 10, 50), 200)
        except (ValueError, TypeError): pass
        new_params["Limit"] = fetch_limit

    required_fields = set(["ProviderIds"])
    if post_filter_rules:
        for rule in post_filter_rules: required_fields.add(rule.field.split('.')[0])
    if required_fields:
        current_fields = set(new_params.get("Fields", "").split(','))
        current_fields.discard('')
        current_fields.update(required_fields)
        new_params["Fields"] = ",".join(sorted(list(current_fields)))

    resource_map = {"collection": "CollectionIds", "tag": "TagIds", "person": "PersonIds", "genre": "GenreIds", "studio": "StudioIds"}
    rm = resource_map.get(found_vlib.resource_type)
    res_ids = found_vlib.resolved_resource_ids() if rm else []
    if rm and len(res_ids) == 1:
        new_params[rm] = res_ids[0]
    elif found_vlib.resource_type == 'all':
        if "ParentId" in new_params:
            del new_params["ParentId"]

    headers_to_forward = _build_headers_to_forward(request)
    target_url = f"{real_emby_url}/emby/Users/{user_id}/Items"

    # --- Source library scoping for latest items ---
    ignore_set = config.disabled_library_ids

    effective_source_libs = list(found_vlib.source_libraries) if found_vlib.source_libraries else []
    if effective_source_libs and ignore_set:
        effective_source_libs = [lid for lid in effective_source_libs if lid not in ignore_set]

    if found_vlib.resource_type == "all" and ignore_set and not effective_source_libs:
        try:
            real_libs = await get_real_libraries_hybrid_mode()
            effective_source_libs = [lib["Id"] for lib in real_libs if lib["Id"] not in ignore_set]
        except Exception as e:
            logger.error(f"Failed to fetch real libraries for ignore filtering: {e}")

    if effective_source_libs and found_vlib.resource_type != "rsshub":
        logger.info(f"HOME_LATEST_HANDLER: Source library scoping for '{found_vlib.name}': {effective_source_libs}")
        # Remove ParentId since we'll set it per source library
        new_params.pop("ParentId", None)
        client_limit_val = int(params.get("Limit", 20))

        if rm and len(res_ids) > 1:
            tasks = [
                _fetch_all_items_for_parent(session, target_url, dict(new_params, **{rm: rid}), pid, headers_to_forward)
                for pid in effective_source_libs
                for rid in res_ids
            ]
        else:
            tasks = [
                _fetch_all_items_for_parent(session, target_url, new_params, pid, headers_to_forward)
                for pid in effective_source_libs
            ]
        results = await asyncio.gather(*tasks)
        all_items = []
        for items in results:
            all_items.extend(items)

        all_items = _deduplicate_by_id(all_items)

        if post_filter_rules:
            all_items = _apply_post_filter(all_items, post_filter_rules, filter_match_all)
        if is_tmdb_merge_enabled:
            all_items = await handler_merger.merge_items_by_tmdb(all_items)

        # Sort by DateCreated descending and limit
        if custom_sort_field and custom_sort_order:
            all_items = _apply_custom_sort(all_items, custom_sort_field, custom_sort_order)
        else:
            all_items.sort(key=lambda x: (x.get("DateCreated") or ""), reverse=True)
        all_items = all_items[:client_limit_val]

        content = json.dumps(all_items).encode('utf-8')
        return Response(content=content, status_code=200, headers={"Content-Type": "application/json"})

    # --- Standard path (no source library scoping) ---
    if rm and len(res_ids) > 1:

        async def _fetch_one_latest(rid: str) -> List[Dict]:
            p = dict(new_params)
            p[rm] = rid
            async with session.get(target_url, params=p, headers=headers_to_forward) as resp:
                if resp.status != 200 or "application/json" not in resp.headers.get("Content-Type", ""):
                    return []
                data = await resp.json()
                return data.get("Items", [])

        parts = await asyncio.gather(*[_fetch_one_latest(rid) for rid in res_ids])
        items_list: List[Dict] = []
        for part in parts:
            items_list.extend(part)
        items_list = _deduplicate_by_id(items_list)
    else:
        logger.debug(f"HOME_LATEST_HANDLER: Forwarding to URL={target_url}, Params={new_params}")

        async with session.get(target_url, params=new_params, headers=headers_to_forward) as resp:
            if resp.status != 200 or "application/json" not in resp.headers.get("Content-Type", ""):
                content = await resp.read()
                return Response(content=content, status_code=resp.status, headers={"Content-Type": resp.headers.get("Content-Type")})

            data = await resp.json()
            items_list = data.get("Items", [])

    if post_filter_rules:
        items_list = _apply_post_filter(items_list, post_filter_rules, filter_match_all)

    if is_tmdb_merge_enabled:
        items_list = await handler_merger.merge_items_by_tmdb(items_list)

    if custom_sort_field and custom_sort_order:
        items_list = _apply_custom_sort(items_list, custom_sort_field, custom_sort_order)

    client_limit_str = params.get("Limit")
    if client_limit_str:
        try:
            final_limit = int(client_limit_str)
            items_list = items_list[:final_limit]
        except (ValueError, TypeError):
            pass

    content = json.dumps(items_list).encode('utf-8')
    return Response(content=content, status_code=200, headers={"Content-Type": "application/json"})
