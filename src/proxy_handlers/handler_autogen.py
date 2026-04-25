# src/proxy_handlers/handler_autogen.py (最终身份修正版)

import asyncio
import logging
import shutil
import random
import os
import hashlib
import time
from pathlib import Path
import aiohttp
from urllib.parse import urlparse

import config_manager
from http_client import create_client_session
import cover_subprocess
from cover_emby_fetch import download_cover_images_emby

logger = logging.getLogger(__name__)

GENERATION_IN_PROGRESS = set()

# 【【【 核心修正1：函数签名改变，接收用户ID和Token 】】】
async def generate_poster_in_background(
    library_id: str,
    user_id: str,
    api_key: str,
    server_id: str | None = None,
):
    """
    在后台异步生成海报。此版本使用触发时传入的身份信息来确保权限正确。
    """
    if library_id in GENERATION_IN_PROGRESS:
        return

    GENERATION_IN_PROGRESS.add(library_id)
    logger.info(f"✅ 已启动库 {library_id} (用户: {user_id}) 的封面自动生成后台任务。")
    
    config = None
    temp_dir = None
    
    try:
        raw_config = config_manager.load_config(apply_active_profile=False)
        active_server = raw_config.get_server_by_id(server_id) if server_id else raw_config.get_admin_active_server()
        if not active_server:
            logger.error(f"后台任务：无法确定 server 上下文，server_id={server_id}")
            return

        # 将目标 server 的 profile 显式投影到本次任务的 config 视图，避免多服务器串库
        config = raw_config.model_copy(deep=False)
        config.sync_active_profile_to_legacy(active_server)
        config.emby_url = active_server.emby_url
        config.emby_api_key = active_server.emby_api_key
        config.emby_server_id = active_server.emby_server_id or config.emby_server_id

        vlib = next((v for v in config.virtual_libraries if v.id == library_id), None)
        if not vlib:
            logger.error(f"后台任务：配置中未找到 vlib {library_id}。")
            return

        # --- 不再需要自己获取用户ID，直接使用传入的 ---
        
        # --- 2. 通过内部请求调用 proxy-core 自身来获取项目 ---
        base_raw = os.environ.get("PROXY_CORE_URL", "http://localhost:8999").rstrip("/")
        parsed = urlparse(base_raw)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        target_port = int(active_server.proxy_port or 8999)
        base = f"{scheme}://{host}:{target_port}"
        internal_proxy_url = f"{base}/emby/Users/{user_id}/Items"
        
        # 使用传入的、保证正确的 api_key
        params = {
            "ParentId": library_id,
            "Limit": 100,
            "Fields": "ImageTags,ProviderIds", # 添加ProviderIds以备不时之需
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Series,Video",
            "X-Emby-Token": api_key,
            "X-Emby-Client": "Proxy (AutoGen)",
            "X-Emby-Device-Name": "ProxyAutoGen",
            "X-Emby-Device-Id": "proxy-autogen-device-id",
            "X-Emby-Client-Version": "4.8.11.0",
        }
        
        internal_headers = {
            'Accept': 'application/json',
            'X-Emby-Authorization': f'Emby UserId="{user_id}", Client="Proxy (AutoGen)", Device="ProxyAutoGen", DeviceId="proxy-autogen-device-id", Version="4.8.11.0", Token="{api_key}"'
        }
        
        items = []
        logger.info(f"后台任务：正在向内部代理 {internal_proxy_url} 请求项目...")
        try:
            async with create_client_session() as session:
                async with session.get(internal_proxy_url, params=params, headers=internal_headers, timeout=60) as response:
                    if response.status == 200:
                        items_dict = await response.json()
                        if isinstance(items_dict, dict): items = items_dict.get("Items", [])
                    else:
                        logger.error(f"后台任务：内部代理请求失败，状态码: {response.status}, 响应: {await response.text()}")
        except Exception as e:
            logger.error(f"后台任务连接内部代理时出错: {e}")

        if not items:
            logger.warning(f"后台任务：根据虚拟库 '{vlib.name}' 的规则，未从代理获取到任何项目。")
            return

        items_with_images = [item for item in items if item.get("ImageTags", {}).get("Primary")]
        if not items_with_images:
            logger.warning(f"后台任务：获取到的 {len(items)} 个项目中，没有带主图的项目，无法为库 '{vlib.name}' 生成封面。")
            return
            
        style_name = config.default_cover_style
        selected_items = random.sample(items_with_images, min(9, len(items_with_images)))

        # --- 3. 下载图片（样式四：1=Fanart 链 + 2～9=Primary 预选；其余样式：全 Primary）---
        output_dir = Path("/app/config/images/")
        output_dir.mkdir(exist_ok=True)
        temp_dir = output_dir / f"temp_autogen_{library_id}"
        temp_dir.mkdir(exist_ok=True)

        async with create_client_session() as session:
            ok = await download_cover_images_emby(
                session,
                config.emby_url or "",
                api_key,
                selected_items,
                temp_dir,
                style_shelf_1=(style_name == "style_shelf_1"),
            )

        if not ok:
            logger.error(f"后台任务：为库 {library_id} 下载封面素材失败。")
            return

        logger.info(f"后台任务：使用默认样式 '{style_name}' 为 '{vlib.name}' 生成封面...")

        if style_name not in cover_subprocess.ALLOWED_COVER_STYLES:
            logger.error(f"后台任务：未知的默认样式名称: {style_name}")
            return

        zh_font_path = config.custom_zh_font_path or "/app/src/assets/fonts/multi_1_zh.ttf"
        en_font_path = config.custom_en_font_path or "/app/src/assets/fonts/multi_1_en.ttf"

        if style_name in ('style_single_1', 'style_single_2'):
            main_image_path = temp_dir / "1.jpg"
            if not main_image_path.is_file():
                logger.error(f"后台任务：无法找到用于单图模式的主素材图片 (1.jpg)。")
                return

        final_path = output_dir / f"{library_id}.jpg"
        job = {
            "style_name": style_name,
            "title_zh": vlib.cover_title_zh or vlib.name,
            "title_en": vlib.cover_title_en or "",
            "zh_font_path": zh_font_path,
            "en_font_path": en_font_path,
            "library_dir": str(temp_dir),
            "output_path": str(final_path),
            "crop_16_9": False,
        }
        try:
            await cover_subprocess.run_cover_worker_job(job)
        except RuntimeError as e:
            logger.error(f"后台任务：封面子进程失败 (库 {library_id}): {e}")
            return
        
        new_image_tag = hashlib.md5(str(time.time()).encode()).hexdigest()
        current_config = config_manager.load_config()
        vlib_found_and_updated = False
        for vlib_in_config in current_config.virtual_libraries:
            if vlib_in_config.id == library_id:
                vlib_in_config.image_tag = new_image_tag
                vlib_found_and_updated = True
                break
        
        if vlib_found_and_updated:
            config_manager.save_config(current_config)
            logger.info(f"🎉 封面自动生成成功！已保存至 {final_path} 并更新了 config.json 的 ImageTag 为 {new_image_tag}")
        else:
            logger.error(f"自动生成封面后，无法在 config.json 中找到虚拟库 {library_id} 以更新 ImageTag。")

    except Exception as e:
        logger.error(f"封面自动生成后台任务发生未知错误: {e}", exc_info=True)
    finally:
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir)
        GENERATION_IN_PROGRESS.remove(library_id)
        logger.info(f"后台任务结束，已释放库 {library_id} 的生成锁。")
