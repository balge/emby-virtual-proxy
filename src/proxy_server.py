# src/proxy_server.py (最终修复版)

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
import aiohttp
from http_client import create_client_session
# 【【【 修改这一行 】】】
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import Dict

# 【【【 同时修改这一行，从 proxy_cache 导入缓存与失效函数 】】】
from proxy_cache import (
    vlib_items_cache,
    clear_vlib_items_cache,
    clear_vlib_page_cache,
    list_user_ids_with_vlib_cache,
)
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # This app may be mounted by multiple uvicorn.Server instances (multi-port).
    # Make lifespan idempotent so we don't double-close the shared session.
    refcnt = getattr(app.state, "_lifespan_refcnt", 0)
    if refcnt == 0 or not hasattr(app.state, "aiohttp_session"):
        app.state.aiohttp_session = create_client_session(cookie_jar=aiohttp.DummyCookieJar())
        logger.info("Global AIOHTTP ClientSession created.")
    app.state._lifespan_refcnt = refcnt + 1

    yield
    app.state._lifespan_refcnt = max(0, getattr(app.state, "_lifespan_refcnt", 1) - 1)
    if app.state._lifespan_refcnt == 0 and hasattr(app.state, "aiohttp_session"):
        await app.state.aiohttp_session.close()
        logger.info("Global AIOHTTP ClientSession closed.")

proxy_app = FastAPI(title="Emby Virtual Proxy - Core", lifespan=lifespan)

covers_dir = Path("/app/config/images")
if covers_dir.is_dir():
    proxy_app.mount("/covers", StaticFiles(directory=str(covers_dir)), name="generated_covers")
    logger.info(f"Successfully mounted generated covers directory at /covers")
else:
    logger.warning(f"Generated covers directory not found at {covers_dir}, skipping mount.")


@proxy_app.get("/api/covers/{item_id}")
async def get_generated_cover_proxy(item_id: str):
    """统一封面读取：按 gif -> png -> jpg 返回，供前端直连 proxy-core 端口使用。"""
    for ext, media in (("gif", "image/gif"), ("png", "image/png"), ("jpg", "image/jpeg")):
        p = covers_dir / f"{item_id}.{ext}"
        if p.is_file():
            return FileResponse(str(p), media_type=media)
    raise HTTPException(status_code=404, detail="Cover not found")


def _local_listen_port_from_scope(scope: dict) -> int | None:
    """
    Return local server listen port for the current connection.
    Works for both HTTP and WebSocket.
    """
    try:
        server = scope.get("server")
        if server and isinstance(server, (list, tuple)) and len(server) >= 2:
            return int(server[1])
    except Exception:
        return None
    return None


def _resolve_server_for_listen_port(config: "config_manager.AppConfig", listen_port: int | None):
    if listen_port is not None:
        s = config.get_server_by_proxy_port(int(listen_port))
        if s is not None:
            return s
    return config.get_admin_active_server()


def _config_for_server(config: "config_manager.AppConfig", server):
    """
    Build a request-scoped AppConfig view whose emby_url/api_key points to the
    server bound to current listen port.
    """
    cfg = config.model_copy(deep=False)
    if server is None:
        return cfg
    # Apply server-scoped settings (virtual libs, real libs, cache settings, webhook, etc.)
    # into the request-local config view, so proxy port truly isolates behavior per server.
    try:
        cfg.sync_active_profile_to_legacy(server)
    except Exception:
        # Keep proxy resilient even if profile is malformed; it will fallback to defaults/top-level.
        pass
    cfg.emby_url = server.emby_url
    cfg.emby_api_key = server.emby_api_key
    cfg.emby_server_id = server.emby_server_id or cfg.emby_server_id
    return cfg

@proxy_app.get("/api/internal/get-cached-items/{library_id}")
async def get_cached_items_for_admin(
    request: Request,
    library_id: str,
    user_id: str | None = Query(None, description="Emby user id; default first server user"),
):
    """
    内部 API：按用户读取虚拟库全量缓存（config/vlib/users/{user}/{vlib}/items.db）。
    """
    raw_config = config_manager.load_config()
    listen_port = _local_listen_port_from_scope(request.scope)
    server = _resolve_server_for_listen_port(raw_config, listen_port)
    config = _config_for_server(raw_config, server)
    request.state.server_id = server.id if server else None
    session = request.app.state.aiohttp_session
    from vlib_cache_manager import resolve_emby_user_id

    uid = await resolve_emby_user_id(session, config, user_id)
    if not uid:
        raise HTTPException(status_code=400, detail="无法解析 Emby 用户，请传入 user_id 查询参数。")
    if not server:
        raise HTTPException(status_code=400, detail="Server not resolved for this proxy port.")
    cached_items = vlib_items_cache.get_for_user(server.id, uid, library_id)
    if cached_items is None:
        logger.warning(f"Admin 请求缓存: 无 user={uid} vlib={library_id}")
        raise HTTPException(
            status_code=404,
            detail="未找到该用户下该虚拟库的缓存。请先用对应账号在客户端浏览该库，或触发刷新。",
        )

    logger.info(f"Admin 读取缓存 user={uid} vlib={library_id} count={len(cached_items)}")
    return JSONResponse(content={"Items": cached_items, "UserId": uid})


@proxy_app.get("/api/internal/vlib-cache-user-ids/{library_id}")
async def internal_list_vlib_cache_user_ids(request: Request, library_id: str):
    """
    列出磁盘上对该虚拟库已有 items.db 的 Emby 用户 Id（目录名排序后顺序）。
    供 Admin 封面：用「第一个曾访问该库的用户」而非 Emby /Users 列表顺序的第一个。
    """
    raw_config = config_manager.load_config()
    listen_port = _local_listen_port_from_scope(request.scope)
    server = _resolve_server_for_listen_port(raw_config, listen_port)
    if not server:
        return JSONResponse(content={"vlib_id": library_id, "user_ids": []})
    uids = list_user_ids_with_vlib_cache(server.id, library_id)
    return JSONResponse(content={"vlib_id": library_id, "user_ids": uids})


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
    Internal endpoint: remove on-disk per-user caches for one virtual library (config/vlib/users/*/vlib_id).
    """
    if not _allow_internal_cache_invalidate(request):
        raise HTTPException(status_code=403, detail="Forbidden")

    raw_config = config_manager.load_config()
    listen_port = _local_listen_port_from_scope(request.scope)
    server = _resolve_server_for_listen_port(raw_config, listen_port)
    if not server:
        raise HTTPException(status_code=400, detail="Server not resolved for this proxy port.")
    items_stats = clear_vlib_items_cache(server.id, library_id)
    clear_vlib_page_cache(library_id)
    return JSONResponse(content={"vlib_id": library_id, "items": items_stats})


@proxy_app.post("/api/internal/set-cached-items/{library_id}")
async def internal_set_cached_items(request: Request, library_id: str):
    """
    Internal endpoint: write slimmed items for one user + virtual library on disk.
    Body: {"Items": [...], "UserId": "<optional; default first Emby user>"}
    """
    if not _allow_internal_cache_invalidate(request):
        raise HTTPException(status_code=403, detail="Forbidden")

    body = await request.json()
    items = body.get("Items", [])
    raw_config = config_manager.load_config()
    listen_port = _local_listen_port_from_scope(request.scope)
    server = _resolve_server_for_listen_port(raw_config, listen_port)
    config = _config_for_server(raw_config, server)
    session = request.app.state.aiohttp_session
    from vlib_cache_manager import resolve_emby_user_id

    uid = await resolve_emby_user_id(session, config, body.get("UserId"))
    if not uid:
        raise HTTPException(status_code=400, detail="无法解析 Emby 用户，请在 Body 中提供 UserId。")
    if not server:
        raise HTTPException(status_code=400, detail="Server not resolved for this proxy port.")
    vlib_items_cache.set_for_user(server.id, uid, library_id, items)
    logger.info(f"Internal set-cached-items: user={uid} vlib={library_id} count={len(items)}")
    return JSONResponse(content={"vlib_id": library_id, "UserId": uid, "count": len(items)})


@proxy_app.get("/api/internal/cache-exists/{library_id}")
async def internal_cache_exists(
    request: Request,
    library_id: str,
    user_id: str | None = Query(None),
):
    """Check if on-disk cache exists for user + virtual library."""
    raw_config = config_manager.load_config()
    listen_port = _local_listen_port_from_scope(request.scope)
    server = _resolve_server_for_listen_port(raw_config, listen_port)
    config = _config_for_server(raw_config, server)
    session = request.app.state.aiohttp_session
    from vlib_cache_manager import resolve_emby_user_id

    uid = await resolve_emby_user_id(session, config, user_id)
    if not uid:
        return JSONResponse(content={"exists": False, "UserId": None})
    if not server:
        return JSONResponse(content={"exists": False, "UserId": uid})
    exists = vlib_items_cache.has_for_user(server.id, uid, library_id)
    return JSONResponse(content={"exists": exists, "UserId": uid})


@proxy_app.post("/api/internal/refresh-vlib-cache/{library_id}")
async def internal_refresh_vlib_cache(
    request: Request,
    library_id: str,
    user_id: str | None = Query(
        None,
        description="Single Emby user (optional). If set, only this user is refreshed.",
    ),
):
    """
    Refresh on-disk cache from Emby.

    - JSON body ``{"user_ids": ["...", ...]}``: refresh exactly those users (e.g. after invalidate).
      Empty list → no-op.
    - Query ``user_id=``: refresh one user only.
    - Otherwise: refresh every user who **currently** has a cache file for this vlib
      (no cache on disk → nothing to do).
    """
    if not _allow_internal_cache_invalidate(request):
        raise HTTPException(status_code=403, detail="Forbidden")

    raw_config = config_manager.load_config()
    listen_port = _local_listen_port_from_scope(request.scope)
    server = _resolve_server_for_listen_port(raw_config, listen_port)
    config = _config_for_server(raw_config, server)
    vlib = next((v for v in config.virtual_libraries if v.id == library_id), None)
    if not vlib:
        raise HTTPException(status_code=404, detail="Virtual library not found")

    session = request.app.state.aiohttp_session
    from vlib_cache_manager import refresh_vlib_cache

    body_user_ids = None
    raw = await request.body()
    if raw:
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict) and "user_ids" in parsed:
                u = parsed.get("user_ids")
                if u is not None and not isinstance(u, list):
                    raise HTTPException(status_code=400, detail="user_ids must be a JSON array")
                body_user_ids = [str(x) for x in u] if isinstance(u, list) else []
        except HTTPException:
            raise
        except Exception:
            pass

    if body_user_ids is not None:
        to_refresh = body_user_ids
    elif user_id:
        to_refresh = [user_id]
    else:
        if not server:
            to_refresh = []
        else:
            to_refresh = list_user_ids_with_vlib_cache(server.id, library_id)

    if not to_refresh:
        logger.info(f"Internal refresh vlib={library_id}: no users to refresh")
        return JSONResponse(
            content={"vlib_id": library_id, "counts": {}, "refreshed_users": 0},
        )

    counts: Dict[str, int] = {}
    for uid in to_refresh:
        try:
            if not server:
                counts[uid] = 0
            else:
                counts[uid] = await refresh_vlib_cache(
                    vlib, config, session=session, user_id=uid, server_id=server.id
                )
        except Exception as e:
            logger.error(f"refresh_vlib_cache user={uid} vlib={library_id}: {e}", exc_info=True)
            counts[uid] = 0

    total = sum(counts.values())
    logger.info(
        f"Internal refresh vlib={library_id} users={len(to_refresh)} total_items={total}"
    )
    return JSONResponse(
        content={
            "vlib_id": library_id,
            "counts": counts,
            "refreshed_users": len(to_refresh),
            "total": total,
        },
    )

# --- 【【【 核心修复：重写 WebSocket 代理 】】】 ---
@proxy_app.websocket("/{full_path:path}")
async def websocket_proxy(client_ws: WebSocket, full_path: str):
    await client_ws.accept()
    raw_config = config_manager.load_config()
    listen_port = _local_listen_port_from_scope(client_ws.scope)
    server = _resolve_server_for_listen_port(raw_config, listen_port)
    config = _config_for_server(raw_config, server)
    if not config.emby_url:
        await client_ws.close(code=1011)
        return
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
    raw_config = config_manager.load_config()
    listen_port = _local_listen_port_from_scope(request.scope)
    server = _resolve_server_for_listen_port(raw_config, listen_port)
    config = _config_for_server(raw_config, server)
    request.state.server_id = server.id if server else None
    if not config.emby_url:
        raise HTTPException(status_code=400, detail="Emby server is not configured for this proxy port.")
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
    if not response:
        response = await handler_default.forward_request(
            request,
            full_path,
            request.method,
            real_emby_url,
            session,
            enable_api_cache=config.enable_cache,
        )

    return response
