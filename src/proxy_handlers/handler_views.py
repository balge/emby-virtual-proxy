# src/proxy_handlers/handler_views.py

import json
import logging
from fastapi import Request, Response
from aiohttp import ClientSession
from models import AppConfig
import asyncio
from pathlib import Path
from cover_identity import cover_path_for, extract_user_id_from_request, resolve_cover_tag

# 【新增】导入后台生成处理器和任务锁
from . import handler_autogen

logger = logging.getLogger(__name__)

# 【新增】定义封面存储路径
COVERS_DIR = Path("/app/config/images")


def _has_local_cover(vlib, user_id: str | None = None) -> bool:
    for ext in ("gif", "png", "jpg"):
        if cover_path_for(COVERS_DIR, vlib.id, vlib.resource_type, user_id, ext).is_file():
            return True
    return False

async def handle_view_injection(
    request: Request,
    full_path: str,
    method: str,
    real_emby_url: str,
    session: ClientSession,
    config: AppConfig
) -> Response | None:
    if "Users" not in full_path or "/Views" not in full_path or method != "GET":
        return None

    if not config.display_order:
        # 旧版逻辑可以保持原样，或者也进行相应修改，但我们主要关注新版
        return await legacy_handle_view_injection(request, full_path, method, real_emby_url, session, config)

    logger.info(f"Full layout control enabled. Intercepting views for path: {full_path}")
    
    target_url = f"{real_emby_url}/{full_path}"
    params = request.query_params
    request_user_id = extract_user_id_from_request(request, full_path)
    
    # 采用更稳健的白名单策略转发必要的请求头，确保认证信息不丢失
    headers_to_forward = {
        k: v for k, v in request.headers.items() 
        if k.lower() in [
            'accept', 'accept-language', 'user-agent',
            'x-emby-authorization', 'x-emby-client', 'x-emby-device-name',
            'x-emby-device-id', 'x-emby-client-version', 'x-emby-language',
            'x-emby-token'
        ]
    }

    async with session.get(target_url, params=params, headers=headers_to_forward) as resp:
        if resp.status != 200 or "application/json" not in resp.headers.get("Content-Type", ""):
            return None

        original_data = await resp.json()

        ignore_set = config.disabled_library_ids
        emby_items = original_data.get("Items", []) or []
        if ignore_set:
            emby_items = [item for item in emby_items if item.get("Id") not in ignore_set]

        all_available_libs = {item["Id"]: item for item in emby_items}
        
        server_id = next((item.get("ServerId") for item in all_available_libs.values() if item.get("ServerId")), "unknown")

        for vlib in config.virtual_libraries:
            if vlib.hidden:
                continue
            vlib_data = {
                "Name": vlib.name, "ServerId": server_id, "Id": vlib.id,
                "Type": "CollectionFolder",
                "CollectionType": "tvshows",
                "IsFolder": True,
                "ImageTags": {}
            }
            
            # 【【【 核心修改：在这里决定是否触发自动生成 】】】
            cover_user_id = request_user_id if vlib.resource_type == "random" else None
            if _has_local_cover(vlib, cover_user_id):
                # 图片已存在, 注入真实的 ImageTag
                image_tag = resolve_cover_tag(vlib, cover_user_id, fallback_to_default=False)
                if image_tag:
                    vlib_data["ImageTags"]["Primary"] = image_tag
                    vlib_data["HasPrimaryImage"] = True
            else:
                # 图片不存在, 触发后台生成并注入假Tag
                logger.info(f"主页视图：发现虚拟库 '{vlib.name}' ({vlib.id}) 缺少封面。")
                
                # 检查任务是否已在运行，防止重复触发
                generation_key = handler_autogen.generation_key(vlib.id, request_user_id, vlib.resource_type)
                if generation_key not in handler_autogen.GENERATION_IN_PROGRESS:
                    # 【【【 核心修正2：传递用户ID和API Key 】】】
                    # 从当前请求中提取必要信息
                    user_id = params.get("UserId")
                    if not user_id: # 如果参数里没有，就从路径里找
                        path_parts = full_path.split('/')
                        if 'Users' in path_parts:
                            user_id = path_parts[path_parts.index('Users') + 1]
                    api_key = params.get("X-Emby-Token") or config.emby_api_key
                    server_id_ctx = getattr(getattr(request, "state", None), "server_id", None)

                    if user_id and api_key:
                        asyncio.create_task(
                            handler_autogen.generate_poster_in_background(
                                vlib.id,
                                user_id,
                                api_key,
                                server_id=server_id_ctx,
                            )
                        )
                    else:
                        logger.warning(f"无法为库 {vlib.id} 触发后台任务，因为缺少 UserId 或 ApiKey。")

                vlib_data["ImageTags"]["Primary"] = "generating_placeholder"
                vlib_data["HasPrimaryImage"] = True

            all_available_libs[vlib.id] = vlib_data

        sorted_items = [all_available_libs[lib_id] for lib_id in config.display_order if lib_id in all_available_libs]
        
        original_data["Items"] = sorted_items
        original_data["TotalRecordCount"] = len(sorted_items)
        
        final_content = json.dumps(original_data).encode('utf-8')
        return Response(content=final_content, status_code=200, media_type="application/json")


async def legacy_handle_view_injection(request: Request, full_path: str, method: str, real_emby_url: str, session: ClientSession, config: AppConfig):
    target_url = f"{real_emby_url}/{full_path}"
    params = request.query_params

    # 同样在此处采用白名单策略
    headers_to_forward = {
        k: v for k, v in request.headers.items() 
        if k.lower() in [
            'accept', 'accept-language', 'user-agent',
            'x-emby-authorization', 'x-emby-client', 'x-emby-device-name',
            'x-emby-device-id', 'x-emby-client-version', 'x-emby-language',
            'x-emby-token'
        ]
    }
    
    async with session.get(target_url, params=params, headers=headers_to_forward) as resp:
        if resp.status == 200 and "application/json" in resp.headers.get("Content-Type", ""):
            content_json = await resp.json()
            if content_json.get("Items"):
                if config.hide:
                    content_json["Items"] = [item for item in content_json["Items"] if item.get("CollectionType") not in config.hide]

                ignore_set = config.disabled_library_ids
                if ignore_set:
                    content_json["Items"] = [
                        item for item in content_json["Items"] if item.get("Id") not in ignore_set
                    ]

                # 【【【 已删除 is_hidden 判断 】】】
                sorted_virtual_libraries = sorted(config.virtual_libraries, key=lambda vlib: getattr(vlib, 'order', 0))
                items_after = content_json["Items"] or []
                server_id = items_after[0].get("ServerId") if items_after else "unknown"

                for vlib in sorted_virtual_libraries:
                    if vlib.hidden:
                        continue
                    if not any(item.get("Id") == vlib.id for item in content_json["Items"]):
                        content_json["Items"].append({
                            "Name": vlib.name, "ServerId": server_id, "Id": vlib.id, 
                            "Type": "CollectionFolder", "CollectionType": "tvshows", 
                            "IsFolder": True, "ImageTags": {}
                        })
                final_content = json.dumps(content_json).encode('utf-8')
                return Response(content=final_content, status_code=200, media_type="application/json")
    return None
