# src/proxy_handlers/handler_images.py (最终修复版)

import logging
import re
from pathlib import Path
from fastapi import Request, Response
from fastapi.responses import FileResponse
import asyncio

from models import AppConfig

logger = logging.getLogger(__name__)

# 【修复 1】修正正则表达式，使其同时匹配 UUID, "tmdb-" (横杠), 和 "tmdb_" (下划线)
IMAGE_PATH_REGEX = re.compile(r"/Items/([a-f0-9\-]{36}|tmdb[-_]\d+)/Images/(\w+)")

COVERS_DIR = Path("/app/config/images")
PLACEHOLDER_GENERATING_PATH = Path("/app/src/assets/images_placeholder/generating.jpg")  # 虚拟库 (UUID)
PLACEHOLDER_RSSHUB_PATH = Path("/app/src/assets/images_placeholder/rsshubpost.jpg")      # RSSHub (tmdb-)
# 【重要】请确保这个路径下存在一个为缺失剧集准备的占位图文件
PLACEHOLDER_EPISODE_PATH = Path("/app/src/assets/images_placeholder/placeholder.jpg")      # 缺失剧集 (tmdb_)


async def handle_virtual_library_image(request: Request, full_path: str, config: AppConfig) -> Response | None:
    match = IMAGE_PATH_REGEX.search(f"/{full_path}")
    if not match:
        return None

    item_id = match.group(1)
    image_type = match.group(2)

    if image_type != "Primary":
        return None

    # 标准虚拟库 UUID 封面：隐藏库不再提供本地封面（避免客户端仍显示海报）
    if len(item_id) == 36 and item_id.count("-") == 4:
        found = next((v for v in config.virtual_libraries if v.id == item_id), None)
        if found and found.hidden:
            logger.info(f"IMAGE_HANDLER: Virtual library '{found.name}' is hidden; 404.")
            return Response(status_code=404)

    # 检查实际的封面文件是否存在，如果存在则直接返回
    image_file = COVERS_DIR / f"{item_id}.jpg"
    if image_file.is_file():
        return FileResponse(str(image_file), media_type="image/jpeg")

    # --- 【修复 2】重构占位图返回逻辑 ---
    # 如果封面文件不存在，则根据 item_id 的格式返回对应的占位图
    # 这种方法比检查 URL 参数更可靠
    
    placeholder_to_serve = None
    
    if item_id.startswith("tmdb_"):  # 缺失剧集的 ID 以 "tmdb_" 开头
        logger.debug(f"IMAGE_HANDLER: Serving MISSING EPISODE placeholder for item '{item_id}'.")
        placeholder_to_serve = PLACEHOLDER_EPISODE_PATH
        
    elif item_id.startswith("tmdb-"):  # RSSHub 项目的 ID 以 "tmdb-" 开头
        logger.debug(f"IMAGE_HANDLER: Serving RSSHUB placeholder for item '{item_id}'.")
        placeholder_to_serve = PLACEHOLDER_RSSHUB_PATH
        
    else:  # 其他情况（标准的 UUID）被认为是常规虚拟库
        logger.debug(f"IMAGE_HANDLER: Serving 'GENERATING' placeholder for virtual library '{item_id}'.")
        placeholder_to_serve = PLACEHOLDER_GENERATING_PATH

    if placeholder_to_serve and placeholder_to_serve.is_file():
        return FileResponse(str(placeholder_to_serve), media_type="image/jpeg")
    
    # 如果连占位图文件本身都不存在，则返回 404
    logger.error(f"IMAGE_HANDLER: Placeholder file not found for item '{item_id}' at path: {placeholder_to_serve}")
    return Response(status_code=404)