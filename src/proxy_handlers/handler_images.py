# src/proxy_handlers/handler_images.py (最终修复版)

import logging
import re
from pathlib import Path
from fastapi import Request, Response
from fastapi.responses import FileResponse

from models import AppConfig
from cover_identity import cover_path_for, extract_user_id_from_request

logger = logging.getLogger(__name__)

# 匹配 Emby 标准图片路径；真实库 ID 在不同 Emby 实例中不一定是带横杠 UUID。
IMAGE_PATH_REGEX = re.compile(r"/Items/([^/?#]+)/Images/(\w+)")

COVERS_DIR = Path("/app/config/images")
PLACEHOLDER_GENERATING_PATH = Path("/app/src/assets/images_placeholder/generating.jpg")  # 虚拟库 (UUID)
PLACEHOLDER_RSSHUB_PATH = Path("/app/src/assets/images_placeholder/rsshubpost.jpg")      # RSSHub (tmdb-)
# 【重要】请确保这个路径下存在一个为缺失剧集准备的占位图文件
PLACEHOLDER_EPISODE_PATH = Path("/app/src/assets/images_placeholder/placeholder.jpg")      # 缺失剧集 (tmdb_)


def _local_cover_response(item_id: str, resource_type: str, user_id: str | None = None) -> FileResponse | None:
    for ext, media_type in (
        ("gif", "image/gif"),
        ("png", "image/png"),
        ("jpg", "image/jpeg"),
    ):
        cover_file = cover_path_for(COVERS_DIR, item_id, resource_type, user_id, ext)
        if cover_file.is_file():
            return FileResponse(str(cover_file), media_type=media_type)
    return None


def _placeholder_response(path: Path, media_type: str = "image/jpeg") -> FileResponse | Response:
    if path.is_file():
        return FileResponse(str(path), media_type=media_type)
    logger.error("IMAGE_HANDLER: Placeholder file not found at path: %s", path)
    return Response(status_code=404)


async def handle_virtual_library_image(request: Request, full_path: str, config: AppConfig) -> Response | None:
    match = IMAGE_PATH_REGEX.search(f"/{full_path}")
    if not match:
        return None

    item_id = match.group(1)
    image_type = match.group(2)

    if image_type != "Primary":
        return None
    request_user_id = extract_user_id_from_request(request, full_path)

    found_vlib = next((v for v in config.virtual_libraries if v.id == item_id), None)
    if found_vlib:
        if found_vlib.hidden:
            logger.info("IMAGE_HANDLER: Virtual library '%s' is hidden; 404.", found_vlib.name)
            return Response(status_code=404)

        cover_user_id = request_user_id if found_vlib.resource_type == "random" else None
        local_cover = _local_cover_response(item_id, found_vlib.resource_type, cover_user_id)
        if not local_cover and found_vlib.resource_type == "random" and cover_user_id:
            local_cover = _local_cover_response(item_id, found_vlib.resource_type, None)
        if local_cover:
            return local_cover

        logger.debug("IMAGE_HANDLER: Serving generating placeholder for virtual library '%s'.", item_id)
        return _placeholder_response(PLACEHOLDER_GENERATING_PATH)

    if item_id.startswith("tmdb_"):
        logger.debug("IMAGE_HANDLER: Serving missing episode placeholder for item '%s'.", item_id)
        return _placeholder_response(PLACEHOLDER_EPISODE_PATH)

    if item_id.startswith("tmdb-"):
        logger.debug("IMAGE_HANDLER: Serving RSSHub placeholder for item '%s'.", item_id)
        return _placeholder_response(PLACEHOLDER_RSSHUB_PATH)

    found_real = next((r for r in config.real_libraries if r.id == item_id), None)
    if found_real:
        local_cover = _local_cover_response(item_id, "real", None)
        if local_cover:
            logger.debug("IMAGE_HANDLER: Serving local real-library cover for '%s'.", item_id)
            return local_cover
        return None

    # 普通 Emby 媒体项图片不接管，继续走默认反代。
    return None
