# src/proxy_server.py (最终修复版)

import asyncio
import logging
import os
from contextlib import asynccontextmanager
import aiohttp
# 【【【 修改这一行 】】】
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import Tuple, Dict

# 【【【 同时修改这一行，从 proxy_cache 导入缓存与失效函数 】】】
from proxy_cache import vlib_items_cache, clear_vlib_items_cache, clear_vlib_page_cache
import config_manager
from proxy_handlers import (
    handler_system, 
    handler_views, 
    handler_items,
    handler_seasons,
    handler_episodes,
    handler_default,
    handler_latest,
    handler_images,
    handler_virtual_items
)

_LOG_LEVEL_NAME = os.environ.get("LOG_LEVEL", "info").upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_NAME, logging.INFO)
logging.basicConfig(level=_LOG_LEVEL, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.info(f"Proxy logger initialized with LOG_LEVEL={_LOG_LEVEL_NAME}")

def get_cache_key(request: Request, full_path: str) -> str:
    if request.method != "GET": return None
    # Skip non-JSON/static-like routes from API cache keying to avoid noisy MISS logs.
    if "/Images/" in full_path or full_path.endswith(".jpg") or full_path.endswith(".png") or full_path.endswith(".webp"):
        return None
    params = dict(request.query_params); params.pop("X-Emby-Token", None); params.pop("api_key", None)
    sorted_params = tuple(sorted(params.items())); user_id_from_path = "public"
    if "/Users/" in full_path:
        try: parts = full_path.split("/"); user_id_from_path = parts[parts.index("Users") + 1]
        except (ValueError, IndexError): pass
    user_id = params.get("UserId", user_id_from_path)
    return f"user:{user_id}:path:{full_path}:params:{sorted_params}"

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.aiohttp_session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()); logger.info("Global AIOHTTP ClientSession created.")
    yield
    await app.state.aiohttp_session.close(); logger.info("Global AIOHTTP ClientSession closed.")

proxy_app = FastAPI(title="Emby Virtual Proxy - Core", lifespan=lifespan)

covers_dir = Path("/app/config/images")
if covers_dir.is_dir():
    proxy_app.mount("/covers", StaticFiles(directory=str(covers_dir)), name="generated_covers")
    logger.info(f"Successfully mounted generated covers directory at /covers")
else:
    logger.warning(f"Generated covers directory not found at {covers_dir}, skipping mount.")

@proxy_app.get("/api/internal/get-cached-items/{library_id}")
async def get_cached_items_for_admin(library_id: str):
    """
    一个内部API，专门用于给admin服务提供已缓存的虚拟库项目列表。
    """
    cached_items = vlib_items_cache.get(library_id)
    if not cached_items:
        logger.warning(f"Admin请求缓存，但未找到库 {library_id} 的缓存。")
        raise HTTPException(
            status_code=404,
            detail="未找到该虚拟库的项目缓存。请先在Emby客户端中实际浏览一次该虚拟库，以生成缓存数据。"
        )
    
    logger.info(f"Admin成功获取到库 {library_id} 的 {len(cached_items)} 条缓存项目。")
    return JSONResponse(content={"Items": cached_items})


def _allow_internal_cache_invalidate(request: Request) -> bool:
    token = os.environ.get("INTERNAL_CACHE_TOKEN", "").strip()
    if token:
        return request.headers.get("x-internal-token", "").strip() == token

    # Default: only allow from localhost (typical admin->proxy within the same container)
    client_host = request.client.host if request.client else ""
    return client_host in ("127.0.0.1", "::1")


@proxy_app.post("/api/internal/invalidate-vlib-cache/{library_id}")
async def internal_invalidate_vlib_cache(request: Request, library_id: str):
    """
    Internal endpoint: drop proxy-process in-memory caches for one virtual library.
    Needed because admin/proxy are separate processes with separate RAM.
    """
    if not _allow_internal_cache_invalidate(request):
        raise HTTPException(status_code=403, detail="Forbidden")

    items_stats = clear_vlib_items_cache(library_id)
    clear_vlib_page_cache(library_id)
    return JSONResponse(content={"vlib_id": library_id, "items": items_stats})


@proxy_app.post("/api/internal/set-cached-items/{library_id}")
async def internal_set_cached_items(request: Request, library_id: str):
    """
    Internal endpoint: write slimmed items into proxy-process vlib_items_cache.
    Called by admin/vlib_cache_manager after fetching from Emby.
    """
    if not _allow_internal_cache_invalidate(request):
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await request.json()
    items = body.get("Items", [])
    vlib_items_cache[library_id] = items
    logger.info(f"Internal set-cached-items: {library_id} = {len(items)} items")
    return JSONResponse(content={"vlib_id": library_id, "count": len(items)})


@proxy_app.get("/api/internal/cache-exists/{library_id}")
async def internal_cache_exists(library_id: str):
    """Check if a vlib cache entry exists in proxy memory."""
    exists = library_id in vlib_items_cache
    return JSONResponse(content={"exists": exists})

# --- 【【【 核心修复：重写 WebSocket 代理 】】】 ---
@proxy_app.websocket("/{full_path:path}")
async def websocket_proxy(client_ws: WebSocket, full_path: str):
    await client_ws.accept()
    config = config_manager.load_config()
    target_url = config.emby_url.replace("http", "ws", 1).rstrip('/') + "/" + full_path
    
    session = proxy_app.state.aiohttp_session
    try:
        headers = {k: v for k, v in client_ws.headers.items() if k.lower() not in ['host', 'connection', 'upgrade', 'sec-websocket-key', 'sec-websocket-version']}
        
        async with session.ws_connect(target_url, params=client_ws.query_params, headers=headers) as server_ws:

            async def forward_client_to_server():
                try:
                    while True:
                        # FastAPI WebSocket 使用 .receive_text() 或 .receive_bytes()
                        data = await client_ws.receive_text()
                        await server_ws.send_str(data)
                except WebSocketDisconnect:
                    logger.debug("Client WebSocket disconnected.")
                except Exception as e:
                    logger.debug(f"Error forwarding C->S: {e}")

            async def forward_server_to_client():
                try:
                    # aiohttp ClientWebSocketResponse 是一个异步迭代器
                    async for msg in server_ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await client_ws.send_text(msg.data)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await client_ws.send_bytes(msg.data)
                except Exception as e:
                    logger.debug(f"Error forwarding S->C: {e}")

            # 并发运行两个转发任务
            await asyncio.gather(forward_client_to_server(), forward_server_to_client())

    except Exception as e:
        logger.warning(f"WebSocket proxy error for path '{full_path}': {e}")
    finally:
        try:
            await client_ws.close()
        except Exception:
            pass
# --- 【【【 修复结束 】】】 ---


@proxy_app.api_route("/{full_path:path}", methods=["GET", "POST", "DELETE", "PUT"])
async def reverse_proxy(request: Request, full_path: str):
    config = config_manager.load_config()
    real_emby_url = config.emby_url.rstrip('/')

    proxy_address = f"{request.url.scheme}://{request.url.netloc}"
    session = request.app.state.aiohttp_session
    response = None

    if not response: response = await handler_images.handle_virtual_library_image(request, full_path, config)
    if not response: response = await handler_virtual_items.handle_get_virtual_item_info(request, full_path, config)
    if not response: response = await handler_latest.handle_home_latest_items(request, full_path, request.method, real_emby_url, session, config)
    if not response: response = await handler_system.handle_system_and_playback_info(request, full_path, request.method, real_emby_url, proxy_address, session)
    if not response: response = await handler_episodes.handle_episodes_merge(request, full_path, session, real_emby_url)
    if not response: response = await handler_seasons.handle_seasons_merge(request, full_path, session, real_emby_url)
    if not response: response = await handler_items.handle_virtual_library_items(request, full_path, request.method, real_emby_url, session, config)
    if not response: response = await handler_views.handle_view_injection(request, full_path, request.method, real_emby_url, session, config)
    if not response: response = await handler_default.forward_request(request, full_path, request.method, real_emby_url, session)

    return response
