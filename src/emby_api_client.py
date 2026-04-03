"""
Shared Emby HTTP helpers for admin and proxy.

Keeps real-library discovery out of admin_server so the proxy never imports the full admin app.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import aiohttp
from fastapi import HTTPException

import config_manager

logger = logging.getLogger(__name__)


async def fetch_from_emby(endpoint: str, params: Optional[Dict[str, Any]] = None) -> List:
    """GET /emby{endpoint}; returns Items list, or list body, or []. Raises HTTPException on failure."""
    config = config_manager.load_config()
    if not config.emby_url or not config.emby_api_key:
        raise HTTPException(status_code=400, detail="请在系统设置中配置Emby服务器地址和API密钥。")

    headers = {"X-Emby-Token": config.emby_api_key, "Accept": "application/json"}
    url = f"{config.emby_url.rstrip('/')}/emby{endpoint}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=15) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger_msg = (
                        f"从Emby获取数据失败 (Endpoint: {endpoint}, Status: {response.status}): {error_text}"
                    )
                    logger.error(logger_msg)
                    raise HTTPException(status_code=response.status, detail=logger_msg)

                json_response = await response.json()
                if isinstance(json_response, dict):
                    return json_response.get("Items", json_response)
                if isinstance(json_response, list):
                    return json_response
                return []
    except aiohttp.ClientError as e:
        logger_msg = f"连接到Emby时发生网络错误 (Endpoint: {endpoint}): {e}"
        logger.error(logger_msg)
        raise HTTPException(status_code=502, detail=logger_msg) from e
    except HTTPException:
        raise
    except Exception as e:
        logger_msg = f"处理Emby请求时发生未知错误 (Endpoint: {endpoint}): {e}"
        logger.error(logger_msg)
        raise HTTPException(status_code=500, detail=logger_msg) from e


async def get_real_libraries_hybrid_mode() -> List[Dict[str, Any]]:
    """
    Merge MediaFolders + first user's Views into a deduplicated list of
    {Id, Name, CollectionType} (used when source_libraries is empty but ignore filters apply).
    """
    all_real_libs: Dict[str, Dict[str, Any]] = {}
    try:
        media_folders = await fetch_from_emby("/Library/MediaFolders")
        for lib in media_folders:
            lib_id = lib.get("Id")
            if lib_id:
                all_real_libs[lib_id] = {
                    "Id": lib_id,
                    "Name": lib.get("Name"),
                    "CollectionType": lib.get("CollectionType"),
                }
    except HTTPException as e:
        logger.warning("从 /Library/MediaFolders 获取数据失败: %s", e.detail)

    try:
        user_items = await fetch_from_emby("/Users")
        if user_items:
            ref_user_id = user_items[0].get("Id")
            if ref_user_id:
                views = await fetch_from_emby(f"/Users/{ref_user_id}/Views")
                for lib in views:
                    lib_id = lib.get("Id")
                    if lib_id and lib_id not in all_real_libs:
                        all_real_libs[lib_id] = {
                            "Id": lib_id,
                            "Name": lib.get("Name"),
                            "CollectionType": lib.get("CollectionType"),
                        }
    except HTTPException as e:
        logger.warning("从 /Users/.../Views 获取数据失败: %s", e.detail)

    return list(all_real_libs.values())
