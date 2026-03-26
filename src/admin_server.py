# src/admin_server.py

import uvicorn
from fastapi import FastAPI, APIRouter, HTTPException, Response, Query, File, UploadFile
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import uuid
import random
import re
import shutil
import os
import aiohttp
import asyncio
import sys
import hashlib
import time
from typing import List, Dict, Optional
from pydantic import BaseModel
import importlib
# 导入封面生成模块
# from cover_generator import style_multi_1 # 改为动态导入
import base64
from PIL import Image
from io import BytesIO
from fastapi import Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import docker
from proxy_cache import api_cache
from models import AppConfig, VirtualLibrary, AdvancedFilter
import config_manager
from db_manager import DBManager, RSS_CACHE_DB

# 【【【 在这里添加或者确认你有这几行 】】】
import logging
from proxy_handlers._filter_translator import translate_rules

# 设置日志记录器
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# 【【【 添加/确认结束 】】】

scheduler = AsyncIOScheduler()

admin_app = FastAPI(title="Emby Virtual Proxy - Admin API")
api_router = APIRouter(prefix="/api")

# Define path for the library items DB, as it's not in db_manager
RSS_LIBRARY_ITEMS_DB = Path("/app/config/rss_library_items.db")

class CoverRequest(BaseModel):
    library_id: str
    title_zh: str # 之前是 library_name，现在改为 title_zh
    title_en: Optional[str] = None
    style_name: str # 新增：用于指定封面样式
    temp_image_paths: Optional[List[str]] = None

# --- 高级筛选器 API ---
@api_router.get("/advanced-filters", response_model=List[AdvancedFilter], tags=["Advanced Filters"])
async def get_advanced_filters():
    """获取所有高级筛选器规则"""
    return config_manager.load_config().advanced_filters

@api_router.post("/advanced-filters", status_code=204, tags=["Advanced Filters"])
async def save_advanced_filters(filters: List[AdvancedFilter]):
    """保存所有高级筛选器规则"""
    # 这是一个全新的、更健壮的实现
    try:
        # 1. 加载当前配置的原始字典数据
        current_config_dict = config_manager.load_config().model_dump()
        
        # 2. 将接收到的筛选器（它们是Pydantic模型）转换为字典列表
        filters_dict_list = [f.model_dump() for f in filters]
        
        # 3. 更新字典中的 'advanced_filters' 键
        current_config_dict['advanced_filters'] = filters_dict_list
        
        # 4. 使用更新后的完整字典来验证并创建一个新的 AppConfig 模型
        new_config = AppConfig.model_validate(current_config_dict)
        
        # 5. 保存这个全新的、有效的配置对象
        config_manager.save_config(new_config)
        
        return Response(status_code=204)
    except Exception as e:
        # 打印详细错误以供调试
        print(f"保存高级筛选器时发生严重错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 【【【 最终版本的 _fetch_images_from_vlib 函数 】】】
async def _fetch_images_from_vlib(library_id: str, temp_dir: Path, config: AppConfig):
    """
    从 proxy-core 的内部缓存中获取项目列表，并下载封面。
    (最终架构版)
    """
    logger.info(f"开始从内部缓存为虚拟库 {library_id} 获取封面素材...")

    proxy_core_url = os.getenv("PROXY_CORE_URL")
    if not proxy_core_url:
        raise HTTPException(status_code=500, detail="环境变量 PROXY_CORE_URL 未设置。")

    # --- 1. 向 proxy-core 请求缓存数据 ---
    target_url = f"{proxy_core_url.rstrip('/')}/api/internal/get-cached-items/{library_id}"
    items = []
    
    logger.info(f"正在向内部代理服务 {target_url} 请求缓存数据...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(target_url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get("Items", [])
                else:
                    error_detail = (await response.json()).get("detail")
                    logger.error(f"从内部代理获取缓存失败 (Status: {response.status}): {error_detail}")
                    raise HTTPException(status_code=response.status, detail=error_detail)
    except aiohttp.ClientError as e:
        logger.error(f"连接到内部代理服务时出错: {e}")
        raise HTTPException(status_code=502, detail=f"无法连接到内部代理服务: {e}")

    # --- 2. 后续处理 ---
    items_with_images = [item for item in items if item.get("ImageTags", {}).get("Primary")]

    if not items_with_images:
        raise HTTPException(status_code=404, detail="缓存数据中不包含任何带有主封面的项目。")

    selected_items = random.sample(items_with_images, min(9, len(items_with_images)))

    # --- 并发下载图片 ---
    async def download_image(session, item, index):
        image_url = f"{config.emby_url.rstrip('/')}/emby/Items/{item['Id']}/Images/Primary"
        headers = {'X-Emby-Token': config.emby_api_key}
        try:
            async with session.get(image_url, headers=headers, timeout=20) as response:
                if response.status == 200:
                    content = await response.read()
                    image_path = temp_dir / f"{index}.jpg"
                    with open(image_path, "wb") as f:
                        f.write(content)
                    return True
        except Exception:
            return False

    async with aiohttp.ClientSession() as session:
        tasks = [download_image(session, item, i + 1) for i, item in enumerate(selected_items)]
        results = await asyncio.gather(*tasks)

    if not any(results):
        raise HTTPException(status_code=500, detail="所有封面素材下载失败，无法生成海报。")

@api_router.post("/upload_temp_image", tags=["Cover Generator"])
async def upload_temp_image(file: UploadFile = File(...)):
    TEMP_IMAGE_DIR = Path("/app/config/temp_images")
    TEMP_IMAGE_DIR.mkdir(exist_ok=True)
    
    # Sanitize filename
    filename = re.sub(r'[^a-zA-Z0-9._-]', '', file.filename)
    unique_filename = f"{uuid.uuid4()}_{filename}"
    file_path = TEMP_IMAGE_DIR / unique_filename
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Return the path that the frontend can use
        return {"path": str(file_path)}
    except Exception as e:
        logger.error(f"上传临时图片失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="文件上传处理失败。")

async def _fetch_images_from_custom_path(custom_path: str, temp_dir: Path):
    """从自定义目录随机复制图片到临时目录"""
    logger.info(f"开始从自定义目录 {custom_path} 获取封面素材...")
    
    source_dir = Path(custom_path)
    if not source_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"自定义图片目录不存在: {custom_path}")

    supported_formats = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")
    image_files = [f for f in source_dir.iterdir() if f.is_file() and f.suffix.lower() in supported_formats]

    if not image_files:
        raise HTTPException(status_code=404, detail=f"自定义图片目录中未找到支持的图片文件: {custom_path}")

    selected_files = random.sample(image_files, min(9, len(image_files)))

    for i, file_path in enumerate(selected_files):
        try:
            dest_path = temp_dir / f"{i + 1}{file_path.suffix}"
            shutil.copy(file_path, dest_path)
        except Exception as e:
            logger.error(f"复制文件 {file_path} 时出错: {e}")
            continue
    
    # 检查是否至少成功复制了一张图片
    if not any(temp_dir.iterdir()):
        raise HTTPException(status_code=500, detail="从自定义目录复制图片素材失败。")

# --- 辅助函数：健壮地获取 Emby 数据 ---
async def _fetch_from_emby(endpoint: str, params: Dict = None) -> List:
    config = config_manager.load_config()
    if not config.emby_url or not config.emby_api_key:
        raise HTTPException(status_code=400, detail="请在系统设置中配置Emby服务器地址和API密钥。")
    
    headers = {'X-Emby-Token': config.emby_api_key, 'Accept': 'application/json'}
    url = f"{config.emby_url.rstrip('/')}/emby{endpoint}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=15) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger_msg = f"从Emby获取数据失败 (Endpoint: {endpoint}, Status: {response.status}): {error_text}"
                    print(f"[PROXY-ADMIN-ERROR] {logger_msg}")
                    raise HTTPException(status_code=response.status, detail=logger_msg)
                
                json_response = await response.json()
                if isinstance(json_response, dict):
                    return json_response.get("Items", json_response)
                elif isinstance(json_response, list):
                    return json_response
                else:
                    return []
    except aiohttp.ClientError as e:
        logger_msg = f"连接到Emby时发生网络错误 (Endpoint: {endpoint}): {e}"
        print(f"[PROXY-ADMIN-ERROR] {logger_msg}")
        raise HTTPException(status_code=502, detail=logger_msg)
    except Exception as e:
        logger_msg = f"处理Emby请求时发生未知错误 (Endpoint: {endpoint}): {e}"
        print(f"[PROXY-ADMIN-ERROR] {logger_msg}")
        raise HTTPException(status_code=500, detail=logger_msg)

async def get_real_libraries_hybrid_mode() -> List:
    all_real_libs = {}
    try:
        media_folders = await _fetch_from_emby("/Library/MediaFolders")
        for lib in media_folders:
            lib_id = lib.get("Id")
            if lib_id:
                all_real_libs[lib_id] = {
                    "Id": lib_id, "Name": lib.get("Name"), "CollectionType": lib.get("CollectionType")
                }
    except HTTPException as e:
        print(f"[PROXY-ADMIN-WARNING] 从 /Library/MediaFolders 获取数据失败: {e.detail}")

    try:
        user_items = await _fetch_from_emby("/Users")
        if user_items:
            ref_user_id = user_items[0].get("Id")
            if ref_user_id:
                views = await _fetch_from_emby(f"/Users/{ref_user_id}/Views")
                for lib in views:
                    lib_id = lib.get("Id")
                    if lib_id and lib_id not in all_real_libs:
                        all_real_libs[lib_id] = {
                           "Id": lib_id, "Name": lib.get("Name"), "CollectionType": lib.get("CollectionType")
                        }
    except HTTPException as e:
         print(f"[PROXY-ADMIN-WARNING] 从 /Users/.../Views 获取数据失败: {e.detail}")

    return list(all_real_libs.values())


# --- API ---
@api_router.get("/config", response_model=AppConfig, response_model_by_alias=True, tags=["Configuration"])
async def get_config():
    return config_manager.load_config()

# 修改 update_config
@api_router.post("/config", response_model=AppConfig, response_model_by_alias=True, tags=["Configuration"])
async def update_config(config: AppConfig):
    config_manager.save_config(config)
    # 更新定时任务
    update_rss_refresh_job(config)
    return config

# 将 /cache/clear 路径改为 /proxy/restart，功能也彻底改变
@api_router.post("/proxy/restart", status_code=204, tags=["System Management"])
async def restart_proxy_container():
    """
    通过 Docker API 重启代理服务器容器。
    这会自动清除其内存中的所有缓存。
    """
    proxy_container_name = os.getenv("PROXY_CONTAINER_NAME")
    if not proxy_container_name:
        print("[PROXY-ADMIN-ERROR] 环境变量 PROXY_CONTAINER_NAME 未设置。")
        raise HTTPException(
            status_code=500,
            detail="Admin服务未配置目标容器名称，无法执行重启。"
        )

    try:
        # 通过挂载的 socket 连接到 Docker 守护进程
        client = docker.from_env()
        
        print(f"[PROXY-ADMIN-INFO] 正在查找要重启的容器: '{proxy_container_name}'")
        proxy_container = client.containers.get(proxy_container_name)
        
        print(f"[PROXY-ADMIN-INFO] 找到容器，正在执行重启...")
        proxy_container.restart()
        
        print(f"[PROXY-ADMIN-INFO] 重启命令已发送至容器 '{proxy_container_name}'。")
        return Response(status_code=204)

    except docker.errors.NotFound:
        print(f"[PROXY-ADMIN-ERROR] 找不到名为 '{proxy_container_name}' 的容器。")
        raise HTTPException(
            status_code=404,
            detail=f"找不到名为 '{proxy_container_name}' 的容器。请检查 docker-compose.yml 配置。"
        )
    except docker.errors.APIError as e:
        print(f"[PROXY-ADMIN-ERROR] 与 Docker API 通信时出错: {e}")
        raise HTTPException(
            status_code=502, # Bad Gateway，表示代理角色执行上游操作失败
            detail=f"与 Docker API 通信时出错: {e}"
        )
    except Exception as e:
        print(f"[PROXY-ADMIN-ERROR] 重启容器时发生未知错误: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"重启容器时发生未知内部错误: {e}"
        )

async def refresh_rss_library_internal(vlib: VirtualLibrary):
    """内部刷新逻辑，供手动和定时任务调用"""
    try:
        if vlib.rss_type == "douban":
            from rss_processor.douban import DoubanProcessor
            processor = DoubanProcessor(vlib)
            processor.process()
        elif vlib.rss_type == "bangumi":
            from rss_processor.bangumi import BangumiProcessor
            processor = BangumiProcessor(vlib)
            processor.process()
        else:
            logger.warning(f"Unknown RSS type: {vlib.rss_type} for library {vlib.id}")
    except Exception as e:
        logger.error(f"Error refreshing RSS library {vlib.id}: {e}", exc_info=True)

@api_router.post("/libraries/{library_id}/refresh", status_code=202, tags=["Libraries"])
async def refresh_rss_library(library_id: str, request: Request):
    """手动触发 RSS 虚拟库的刷新"""
    config = config_manager.load_config()
    vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)

    if not vlib:
        raise HTTPException(status_code=404, detail="Virtual library not found")
    if vlib.resource_type != "rsshub":
        raise HTTPException(status_code=400, detail="This library is not an RSSHUB library")

    # 在后台任务中运行刷新，避免阻塞 API
    asyncio.create_task(refresh_rss_library_internal(vlib))
    
    return {"message": "RSS library refresh process has been started."}


async def _regenerate_cover_for_vlib(vlib: VirtualLibrary):
    """Regenerate cover for a single virtual library using its saved settings."""
    config = config_manager.load_config()
    style_name = config.default_cover_style
    title_zh = vlib.cover_title_zh or vlib.name
    title_en = vlib.cover_title_en or ""
    try:
        image_tag = await _generate_library_cover(vlib.id, title_zh, title_en, style_name)
        if image_tag:
            current_config = config_manager.load_config()
            for v in current_config.virtual_libraries:
                if v.id == vlib.id:
                    v.image_tag = image_tag
                    break
            config_manager.save_config(current_config)
            logger.info(f"Cover regenerated for '{vlib.name}', tag={image_tag}")
        else:
            logger.warning(f"Cover generation returned None for '{vlib.name}'")
    except Exception as e:
        logger.error(f"Failed to regenerate cover for '{vlib.name}': {e}", exc_info=True)


@api_router.post("/libraries/{library_id}/refresh-cover", status_code=202, tags=["Libraries"])
async def refresh_library_cover(library_id: str):
    """Regenerate cover for a single virtual library."""
    config = config_manager.load_config()
    vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)
    if not vlib:
        raise HTTPException(status_code=404, detail="Virtual library not found")
    asyncio.create_task(_regenerate_cover_for_vlib(vlib))
    return {"message": f"Cover refresh started for '{vlib.name}'."}


@api_router.post("/libraries/{library_id}/refresh-data", status_code=202, tags=["Libraries"])
async def refresh_library_data(library_id: str):
    """Refresh data for a single virtual library, then regenerate its cover."""
    config = config_manager.load_config()
    vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)
    if not vlib:
        raise HTTPException(status_code=404, detail="Virtual library not found")

    async def _refresh_data_and_cover():
        # 1. Clear relevant caches
        from proxy_cache import random_recommend_cache, vlib_items_cache, api_cache
        # Clear api_cache entries related to this library
        keys_to_remove = [k for k in api_cache if library_id in str(k)]
        for k in keys_to_remove:
            api_cache.pop(k, None)
        vlib_items_cache.pop(library_id, None)
        # Clear random cache for all users for this vlib
        random_keys = [k for k in random_recommend_cache if k.endswith(f":{library_id}")]
        for k in random_keys:
            random_recommend_cache.pop(k, None)

        # 2. Type-specific data refresh
        if vlib.resource_type == "rsshub":
            await refresh_rss_library_internal(vlib)

        # 3. Regenerate cover
        await _regenerate_cover_for_vlib(vlib)

    asyncio.create_task(_refresh_data_and_cover())
    return {"message": f"Data and cover refresh started for '{vlib.name}'."}


@api_router.post("/covers/refresh-all", status_code=202, tags=["Cover Generator"])
async def refresh_all_covers():
    """Regenerate covers for all virtual libraries."""
    config = config_manager.load_config()
    vlibs = config.virtual_libraries

    async def _refresh_all():
        for vlib in vlibs:
            await _regenerate_cover_for_vlib(vlib)
        logger.info(f"All {len(vlibs)} virtual library covers have been refreshed.")

    asyncio.create_task(_refresh_all())
    return {"message": f"Cover refresh started for {len(vlibs)} virtual libraries."}


@api_router.post("/generate-cover", tags=["Cover Generator"])
async def generate_cover(body: CoverRequest):
    try:
        # 调用核心生成逻辑，并传递样式名称
        image_tag = await _generate_library_cover(body.library_id, body.title_zh, body.title_en, body.style_name, body.temp_image_paths)
        if image_tag:
            # 成功后，需要找到对应的虚拟库并更新其 image_tag
            config = config_manager.load_config()
            vlib_found = False
            for vlib in config.virtual_libraries:
                if vlib.id == body.library_id:
                    vlib.image_tag = image_tag
                    vlib_found = True
                    break
            if vlib_found:
                config_manager.save_config(config)
                return {"success": True, "image_tag": image_tag}
            else:
                raise HTTPException(status_code=404, detail="未找到要更新封面的虚拟库。")
        else:
            raise HTTPException(status_code=500, detail="封面生成失败，详见后端日志。")
    except Exception as e:
        print(f"[COVER-GEN-ERROR] 封面生成过程中发生异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/covers/clear", status_code=204, tags=["Cover Generator"])
async def clear_all_covers():
    """清空所有生成的封面图并重置配置中的 image_tag"""
    covers_dir = Path("/app/config/images")
    logger.info(f"开始清空封面目录: {covers_dir}")
    try:
        if covers_dir.is_dir():
            for item in covers_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        
        # 重置配置
        config = config_manager.load_config()
        for vlib in config.virtual_libraries:
            vlib.image_tag = None
        config_manager.save_config(config)
        
        logger.info("所有封面及配置已成功清除。")
        return Response(status_code=204)
    except Exception as e:
        logger.error(f"清空封面时出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"清空封面时发生内部错误: {e}")

# 封面生成的核心逻辑
async def _generate_library_cover(library_id: str, title_zh: str, title_en: Optional[str], style_name: str, temp_image_paths: Optional[List[str]] = None) -> Optional[str]:
    config = config_manager.load_config()
    # --- 1. 定义路径 ---
    FONT_DIR = "/app/src/assets/fonts/"
    OUTPUT_DIR = "/app/config/images/"
    
    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    
    # 创建一个唯一的临时目录来存放下载的素材
    image_gen_dir = Path(OUTPUT_DIR) / f"temp_{str(uuid.uuid4())}"
    image_gen_dir.mkdir()

    try:
        # --- 2. 【核心改动】: 根据配置选择图片来源 (四级回退) ---
        if temp_image_paths:
            logger.info(f"从 {len(temp_image_paths)} 张上传的临时图片中随机选择素材...")
            selected_paths = random.sample(temp_image_paths, min(9, len(temp_image_paths)))
            for i, src_path_str in enumerate(selected_paths):
                src_path = Path(src_path_str)
                if src_path.is_file():
                    dest_path = image_gen_dir / f"{i + 1}{src_path.suffix}"
                    shutil.copy(src_path, dest_path)
        else:
            vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)
            if vlib and vlib.cover_custom_image_path:
                await _fetch_images_from_custom_path(vlib.cover_custom_image_path, image_gen_dir)
            elif config.custom_image_path:
                await _fetch_images_from_custom_path(config.custom_image_path, image_gen_dir)
            else:
                await _fetch_images_from_vlib(library_id, image_gen_dir, config)

        # --- 3. 【核心改动】: 动态调用所选的样式生成函数 ---
        logger.info(f"素材准备完毕，开始使用样式 '{style_name}' 为 '{title_zh}' ({library_id}) 生成封面...")
        
        try:
            # 动态导入选择的样式模块
            style_module = importlib.import_module(f"cover_generator.{style_name}")
            # 假设每个样式文件都有一个名为 create_... 的主函数
            create_function_name = f"create_{style_name}" 
            create_function = getattr(style_module, create_function_name)
        except (ImportError, AttributeError) as e:
            logger.error(f"无法加载或找到样式生成函数: {style_name} -> {e}")
            raise HTTPException(status_code=400, detail=f"无效的样式名称: {style_name}")

        # --- 字体选择逻辑 ---
        vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)
        
        # 确定中文字体
        if vlib and vlib.cover_custom_zh_font_path:
            zh_font_path = vlib.cover_custom_zh_font_path
        elif config.custom_zh_font_path:
            zh_font_path = config.custom_zh_font_path
        else:
            zh_font_path = os.path.join(FONT_DIR, "ch.ttf")

        # 确定英文字体
        if vlib and vlib.cover_custom_en_font_path:
            en_font_path = vlib.cover_custom_en_font_path
        elif config.custom_en_font_path:
            en_font_path = config.custom_en_font_path
        else:
            en_font_path = os.path.join(FONT_DIR, "en.ttf")

        kwargs = {
            "title": (title_zh, title_en),
            "font_path": (zh_font_path, en_font_path)
        }

        if style_name == 'style_multi_1':
            kwargs['library_dir'] = str(image_gen_dir)
        elif style_name in ['style_single_1', 'style_single_2']:
            # 单图模式，选择第一张图作为主图
            main_image_path = image_gen_dir / "1.jpg"
            if not main_image_path.is_file():
                raise HTTPException(status_code=404, detail="无法找到用于单图模式的主素材图片 (1.jpg)。")
            kwargs['image_path'] = str(main_image_path)
        else:
            raise HTTPException(status_code=400, detail=f"未知的样式名称: {style_name}")

        # 使用关键字参数解包来调用函数
        res = create_function(**kwargs)
        
        if not res:
            logger.error(f"样式函数 {style_name} 返回失败。")
            raise HTTPException(status_code=500, detail=f"封面生成函数 {style_name} 内部错误。")

        # --- 4. 解码、转换并以虚拟库ID为名保存图片 ---
        image_data = base64.b64decode(res)
        img = Image.open(BytesIO(image_data))

        if img.mode != 'RGB':
            img = img.convert('RGB')

        final_filename = f"{library_id}.jpg"
        output_path = os.path.join(OUTPUT_DIR, final_filename)
        
        img.save(output_path, "JPEG", quality=90)
        
        image_tag = hashlib.md5(str(time.time()).encode()).hexdigest()
        
        logger.info(f"封面成功保存至: {output_path}, ImageTag: {image_tag}")

        return image_tag

    except HTTPException as http_exc:
        # 直接向上抛出HTTP异常，以便前端可以显示具体的错误信息
        raise http_exc
    except Exception as e:
        logger.error(f"封面生成过程中发生未知错误: {e}", exc_info=True)
        return None
    finally:
        # --- 5. 【核心改动】: 清理临时目录 ---
        if image_gen_dir.exists():
            shutil.rmtree(image_gen_dir)
            logger.info(f"已清理临时素材目录: {image_gen_dir}")

@api_router.get("/all-libraries", tags=["Display Management"])
async def get_all_libraries():
    all_libs = []
    try:
        real_libs_from_emby = await get_real_libraries_hybrid_mode()
        for lib in real_libs_from_emby:
            all_libs.append({
                "id": lib.get("Id"), "name": lib.get("Name"), "type": "real",
                "collectionType": lib.get("CollectionType")
            })
    except HTTPException as e:
        raise e

    config = config_manager.load_config()
    for lib in config.virtual_libraries:
        all_libs.append({
            "id": lib.id, "name": lib.name, "type": "virtual",
        })
    
    return all_libs
    
@api_router.post("/display-order", status_code=204, tags=["Display Management"])
async def save_display_order(ordered_ids: List[str]):
    config = config_manager.load_config()
    config.display_order = ordered_ids
    config_manager.save_config(config)
    return Response(status_code=204)

@api_router.post("/libraries", response_model=VirtualLibrary, tags=["Libraries"])
async def create_library(library: VirtualLibrary):
    config = config_manager.load_config()
    library.id = str(uuid.uuid4())
    if not hasattr(config, 'virtual_libraries'):
        config.virtual_libraries = []
    
    config.virtual_libraries.append(library)
    if library.id not in config.display_order:
        config.display_order.append(library.id)
        
    config_manager.save_config(config)
    return library

@api_router.put("/libraries/{library_id}", response_model=VirtualLibrary, tags=["Libraries"])
async def update_library(library_id: str, updated_library_data: VirtualLibrary):
    config = config_manager.load_config()
    lib_to_update = None
    for lib in config.virtual_libraries:
        if lib.id == library_id:
            lib_to_update = lib
            break
    
    if not lib_to_update:
        raise HTTPException(status_code=404, detail="Virtual library not found")

    # 【核心修复】: 不完全替换，而是更新字段
    # .model_dump(exclude_unset=True) 只获取客户端实际发送过来的字段
    update_data = updated_library_data.model_dump(exclude_unset=True)
    
    # 【核心修复】在更新前，手动保留旧的 image_tag
    if lib_to_update.image_tag:
        # 如果客户端传来的数据中没有 image_tag，或者为 null，我们强制使用旧的
        if 'image_tag' not in update_data or not update_data['image_tag']:
            update_data['image_tag'] = lib_to_update.image_tag

    # 使用 Pydantic 的 model_copy 方法安全地更新模型
    updated_lib = lib_to_update.model_copy(update=update_data)
    
    # 在列表中替换掉旧的模型
    for i, lib in enumerate(config.virtual_libraries):
        if lib.id == library_id:
            config.virtual_libraries[i] = updated_lib
            break
            
    config_manager.save_config(config)
    return updated_lib

@api_router.delete("/libraries/{library_id}", status_code=204, tags=["Libraries"])
async def delete_library(library_id: str):
    config = config_manager.load_config()
    
    lib_to_delete = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)

    if not lib_to_delete:
        raise HTTPException(status_code=404, detail="Virtual library not found")

    # If it's an RSS library, perform cleanup before deleting
    if lib_to_delete.resource_type == "rsshub":
        logger.info(f"Deleting RSS library '{lib_to_delete.name}'. Performing cleanup...")
        
        # 1. Clean RSS feed cache if URL exists
        if lib_to_delete.rsshub_url:
            try:
                rss_db = DBManager(RSS_CACHE_DB)
                rss_db.execute("DELETE FROM rss_cache WHERE url = ?", (lib_to_delete.rsshub_url,), commit=True)
                logger.info(f"Removed RSS cache for URL: {lib_to_delete.rsshub_url}")
            except Exception as e:
                logger.error(f"Failed to clean RSS cache for {lib_to_delete.rsshub_url}: {e}")

        # 2. Clean associated items from rss_library_items.db
        try:
            # Check if the DB file exists before trying to connect
            if RSS_LIBRARY_ITEMS_DB.is_file():
                rss_items_db = DBManager(RSS_LIBRARY_ITEMS_DB)
                rss_items_db.execute("DELETE FROM rss_library_items WHERE library_id = ?", (library_id,), commit=True)
                logger.info(f"Removed all items for library ID {library_id} from rss_library_items.db")
            else:
                logger.info(f"rss_library_items.db not found, skipping item cleanup for library ID {library_id}.")
        except Exception as e:
            logger.warning(f"Could not clean items for library ID {library_id} from rss_library_items.db: {e}")

    # Proceed with deleting from config
    config.virtual_libraries = [lib for lib in config.virtual_libraries if lib.id != library_id]
    
    if library_id in config.display_order:
        config.display_order.remove(library_id)
        
    config_manager.save_config(config)
    return Response(status_code=204)

@api_router.get("/emby/classifications", tags=["Emby Helper"])
async def get_emby_classifications():
    def format_items(items_list: List) -> List:
        return [{"name": item.get("Name", 'N/A'), "id": item.get("Id", 'N/A')} for item in items_list]

    try:
        tasks = {
            "collections": _fetch_from_emby("/Items", params={"IncludeItemTypes": "BoxSet", "Recursive": "true"}),
            "genres": _fetch_from_emby("/Genres"),
            "tags": _fetch_from_emby("/Tags"),
            "studios": _fetch_from_emby("/Studios"),
        }
        results_list = await asyncio.gather(*tasks.values())
        results_dict = dict(zip(tasks.keys(), results_list))
        results_dict["persons"] = []
        return {key: format_items(value) for key, value in results_dict.items()}
    except HTTPException as e:
        raise e

@api_router.get("/emby/persons/search", tags=["Emby Helper"])
async def search_emby_persons(query: str = Query(None), page: int = Query(1)):
    PAGE_SIZE = 100
    start_index = (page - 1) * PAGE_SIZE
    try:
        if query:
            params = { "SearchTerm": query, "IncludeItemTypes": "Person", "Recursive": "true", "StartIndex": start_index, "Limit": PAGE_SIZE }
            persons = await _fetch_from_emby("/Items", params=params)
        else:
            params = { "StartIndex": start_index, "Limit": PAGE_SIZE }
            persons = await _fetch_from_emby("/Persons", params=params)
        return [{"name": item.get("Name", 'N/A'), "id": item.get("Id", 'N/A')} for item in persons]
    except HTTPException as e:
        raise e

@api_router.get("/emby/resolve-item/{item_id}", tags=["Emby Helper"])
async def resolve_emby_item(item_id: str):
    try:
        users = await _fetch_from_emby("/Users")
        if not users:
            raise HTTPException(status_code=500, detail="无法获取任何Emby用户用于查询")
        
        ref_user_id = users[0]['Id']
        item_details = await _fetch_from_emby(f"/Users/{ref_user_id}/Items/{item_id}")
        
        if not item_details:
             raise HTTPException(status_code=404, detail="在Emby中未找到指定的项目ID")
        
        return {"name": item_details.get("Name", "N/A"), "id": item_details.get("Id")}

    except HTTPException as e:
        raise e

admin_app.include_router(api_router)
static_dir = Path("/app/static")
if not static_dir.is_dir():
    local_static_dir = Path(__file__).parent.parent.parent / "frontend/dist"
    if local_static_dir.is_dir():
        static_dir = local_static_dir
    else:
        sys.exit(1)
if not (static_dir / "index.html").is_file():
     sys.exit(1)

# 新增：挂载生成的封面图目录，使其可以通过 /covers/... 访问
covers_dir = Path("/app/config/images")
covers_dir.mkdir(exist_ok=True) # 确保目录存在
admin_app.mount("/covers", StaticFiles(directory=str(covers_dir)), name="covers")


def update_rss_refresh_job(config: AppConfig):
    job_id = 'rss_refresh_job'
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    
    if config.rss_refresh_interval and config.rss_refresh_interval > 0:
        scheduler.add_job(
            refresh_all_rss_libraries,
            'interval',
            hours=config.rss_refresh_interval,
            id=job_id,
            replace_existing=True
        )
        logger.info(f"RSS refresh job scheduled to run every {config.rss_refresh_interval} hours.")

async def refresh_all_rss_libraries():
    """定时刷新所有 RSS 虚拟库"""
    logger.info("Starting scheduled RSS library refresh...")
    config = config_manager.load_config()
    rss_libraries = [lib for lib in config.virtual_libraries if lib.resource_type == "rsshub"]
    
    for vlib in rss_libraries:
        logger.info(f"Refreshing RSS library: {vlib.name} ({vlib.id})")
        await refresh_rss_library_internal(vlib)
    
    logger.info("Scheduled RSS library refresh finished.")

@admin_app.on_event("startup")
async def startup_event():
    # 启动调度器
    scheduler.start()
    # 初始化定时任务
    config = config_manager.load_config()
    update_rss_refresh_job(config)

@admin_app.on_event("shutdown")
async def shutdown_event():
    # 关闭调度器
    scheduler.shutdown()

admin_app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
