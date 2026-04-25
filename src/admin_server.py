# src/admin_server.py

import uvicorn
from fastapi import FastAPI, APIRouter, HTTPException, Response, Query, File, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
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
import hmac
import time
import secrets
from typing import List, Dict, Optional, Any
from pydantic import BaseModel
import base64
import json
from fastapi import Request
from urllib.parse import urlparse, urlunparse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from proxy_cache import api_cache, vlib_items_cache, slim_items
from models import AppConfig, VirtualLibrary, AdvancedFilter, RealLibraryConfig, WebhookSettings
from emby_webhook import (
    parse_request_payload,
    extract_event_raw,
    SUPPORTED_EVENTS,
    extract_item_dict,
)
import config_manager
from emby_api_client import fetch_from_emby, get_real_libraries_hybrid_mode
from db_manager import DBManager, RSS_CACHE_DB
from http_client import create_client_session
from rss_subprocess import run_rss_refresh_job
from cover_emby_fetch import download_cover_images_emby

# 【【【 在这里添加或者确认你有这几行 】】】
import logging

# 设置日志记录器
logger = logging.getLogger(__name__)


async def _notify_proxy_invalidate_cache(library_id: str, server_id: Optional[str] = None) -> dict:
    """
    通知 proxy 清除该虚拟库在磁盘上的所有用户缓存。
    返回 JSON 中的 items.user_ids：清除**前**已有缓存的用户（供随后按用户重填）。
    """
    out: dict = {"user_ids": []}
    try:
        cfg, resolved_sid = _load_server_scoped_config(server_id)
        active = cfg.get_server_by_id(resolved_sid) if resolved_sid else cfg.get_admin_active_server()
        target_port = int(active.proxy_port) if active else 8999
        base_raw = os.environ.get("PROXY_CORE_URL", "http://localhost:8999").rstrip("/")
        parsed = urlparse(base_raw)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        base = urlunparse((scheme, f"{host}:{target_port}", "", "", "", "")).rstrip("/")
        url = f"{base}/api/internal/invalidate-vlib-cache/{library_id}"
        token = os.environ.get("INTERNAL_CACHE_TOKEN", "").strip()
        headers = {"X-Internal-Token": token} if token else {}
        async with create_client_session() as session:
            async with session.post(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items") or {}
                    out["user_ids"] = list(items.get("user_ids") or [])
                else:
                    text = await resp.text()
                    logger.warning(f"PROXY_INVALIDATE vlib={library_id} HTTP {resp.status}: {text[:300]}")
    except Exception as e:
        logger.warning(f"PROXY_INVALIDATE vlib={library_id} failed: {e}")
    return out


async def _list_vlib_cache_user_ids_from_proxy(library_id: str, server_id: Optional[str] = None) -> list[str]:
    """磁盘上对该虚拟库已有缓存的用户 Id（与 proxy 目录遍历顺序一致，已排序）。"""
    try:
        cfg, resolved_sid = _load_server_scoped_config(server_id)
        active = cfg.get_server_by_id(resolved_sid) if resolved_sid else cfg.get_admin_active_server()
        target_port = int(active.proxy_port) if active else 8999
        base_raw = os.environ.get("PROXY_CORE_URL", "http://localhost:8999").rstrip("/")
        parsed = urlparse(base_raw)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        base = urlunparse((scheme, f"{host}:{target_port}", "", "", "", "")).rstrip("/")
        url = f"{base}/api/internal/vlib-cache-user-ids/{library_id}"
        async with create_client_session() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return list(data.get("user_ids") or [])
    except Exception as e:
        logger.warning(f"list vlib cache users from proxy failed '{library_id}': {e}")
    return []


async def _resolve_cover_cache_user_id(library_id: str, server_id: Optional[str] = None) -> Optional[str]:
    """
    封面素材：优先使用「该库磁盘缓存里第一个用户」（真正访问过该库的人），
    避免 Emby /Users 顺序第一个与缓存所有者不一致导致 404。
    若尚无任何缓存，再退回 Emby 首个用户（与旧行为一致，便于无访问记录时的兜底刷新）。
    """
    scoped, sid = _load_server_scoped_config(server_id)
    uids = await _list_vlib_cache_user_ids_from_proxy(library_id, server_id=sid)
    if uids:
        return uids[0]
    users = await fetch_from_emby("/Users", config=scoped)
    if users:
        return users[0].get("Id")
    return None


async def _get_cached_items_from_proxy(
    library_id: str, user_id: Optional[str] = None, server_id: Optional[str] = None
) -> list[dict] | None:
    """从 proxy 读虚拟库全量缓存。user_id 缺省时按 _resolve_cover_cache_user_id 选取。"""
    try:
        uid = user_id or await _resolve_cover_cache_user_id(library_id, server_id=server_id)
        if not uid:
            return None
        cfg, resolved_sid = _load_server_scoped_config(server_id)
        active = cfg.get_server_by_id(resolved_sid) if resolved_sid else cfg.get_admin_active_server()
        target_port = int(active.proxy_port) if active else 8999
        base_raw = os.environ.get("PROXY_CORE_URL", "http://localhost:8999").rstrip("/")
        parsed = urlparse(base_raw)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        base = urlunparse((scheme, f"{host}:{target_port}", "", "", "", "")).rstrip("/")
        url = f"{base}/api/internal/get-cached-items/{library_id}"
        params = {"user_id": uid}
        async with create_client_session() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("Items", [])
                return None
    except Exception as e:
        logger.warning(f"Failed to get cached items from proxy for '{library_id}': {e}")
        return None


async def _cache_exists_in_proxy(library_id: str, server_id: Optional[str] = None) -> bool:
    """是否存在任意用户对该虚拟库的磁盘缓存。"""
    return len(await _list_vlib_cache_user_ids_from_proxy(library_id, server_id=server_id)) > 0


async def _notify_proxy_refresh_cache(
    library_id: str,
    user_ids: list[str] | None = None,
    server_id: Optional[str] = None,
) -> None:
    """
    通知 proxy 从 Emby 重填缓存。

    - ``user_ids`` 非 None：只刷新这些用户（典型：invalidate 返回的列表；空列表则不刷新任何人）。
    - ``user_ids`` 为 None：刷新**当前磁盘上已有该库缓存**的所有用户（无人访问过则跳过）。
    """
    try:
        cfg, resolved_sid = _load_server_scoped_config(server_id)
        active = cfg.get_server_by_id(resolved_sid) if resolved_sid else cfg.get_admin_active_server()
        target_port = int(active.proxy_port) if active else 8999
        base_raw = os.environ.get("PROXY_CORE_URL", "http://localhost:8999").rstrip("/")
        parsed = urlparse(base_raw)
        host = parsed.hostname or "localhost"
        scheme = parsed.scheme or "http"
        base = urlunparse((scheme, f"{host}:{target_port}", "", "", "", "")).rstrip("/")
        url = f"{base}/api/internal/refresh-vlib-cache/{library_id}"
        token = os.environ.get("INTERNAL_CACHE_TOKEN", "").strip()
        headers = {"X-Internal-Token": token} if token else {}
        post_kw: dict = {"timeout": aiohttp.ClientTimeout(total=600)}
        if user_ids is not None:
            post_kw["json"] = {"user_ids": user_ids}
        async with create_client_session() as session:
            async with session.post(url, headers=headers, **post_kw) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(
                        f"PROXY_REFRESH vlib={library_id} "
                        f"users={data.get('refreshed_users', data.get('count', '?'))} "
                        f"total={data.get('total', data.get('count', '?'))}"
                    )
                else:
                    text = await resp.text()
                    logger.warning(f"PROXY_REFRESH vlib={library_id} HTTP {resp.status}: {text[:300]}")
    except Exception as e:
        logger.warning(f"PROXY_REFRESH vlib={library_id} failed: {e}")


_LOG_LEVEL_NAME = os.environ.get("LOG_LEVEL", "info").upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_NAME, logging.INFO)
logging.basicConfig(level=_LOG_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger.info(f"Admin logger initialized with LOG_LEVEL={_LOG_LEVEL_NAME}")
# 【【【 添加/确认结束 】】】

# ============================================
# 认证配置 - 通过环境变量读取
# ============================================
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
AUTH_ENABLED = bool(ADMIN_USERNAME and ADMIN_PASSWORD)
AUTH_SECRET = os.environ.get("AUTH_SECRET", secrets.token_hex(32))
AUTH_TOKEN_EXPIRY = int(os.environ.get("AUTH_TOKEN_EXPIRY", "86400"))  # 默认24小时

# 存储有效 token 的集合（内存级，重启后失效）
_valid_tokens: Dict[str, float] = {}

def _generate_token() -> str:
    """生成一个带过期时间的认证 token"""
    token = secrets.token_hex(32)
    _valid_tokens[token] = time.time() + AUTH_TOKEN_EXPIRY
    return token

def _verify_token(token: str) -> bool:
    """验证 token 是否有效且未过期"""
    if token not in _valid_tokens:
        return False
    if time.time() > _valid_tokens[token]:
        del _valid_tokens[token]
        return False
    return True

def _cleanup_expired_tokens():
    """清理过期 token"""
    now = time.time()
    expired = [t for t, exp in _valid_tokens.items() if now > exp]
    for t in expired:
        del _valid_tokens[t]

scheduler = AsyncIOScheduler()

# Webhook 延迟合并：收集待刷新的真实库 ID，延迟窗口到期后统一刷新
_webhook_pending_lib_ids_by_server: dict[str, set[str]] = {}
_webhook_pending_task_by_server: dict[str, asyncio.Task] = {}


def _remove_scheduler_jobs_for_server(server_id: str) -> int:
    """
    Best-effort cleanup for jobs that belong to a specific server.
    Supports both legacy and namespaced job ids.
    """
    sid = str(server_id)
    removed = 0
    prefixes = (
        f"vlib_scheduled_refresh:{sid}:",
        f"real_lib_cover_cron:{sid}",
    )
    exact_ids = {
        # legacy ids (single-server mode)
        "real_lib_cover_cron",
    }
    for job in list(scheduler.get_jobs()):
        jid = str(job.id)
        if jid in exact_ids or any(jid.startswith(p) for p in prefixes):
            try:
                scheduler.remove_job(jid)
                removed += 1
            except Exception:
                pass
    if removed:
        logger.info("Removed %s scheduled jobs for server=%s", removed, sid)
    return removed


def _load_server_scoped_config(server_id: Optional[str] = None) -> tuple[AppConfig, Optional[str]]:
    """
    Load config and project one server profile to legacy top-level fields.
    Returns (scoped_config, resolved_server_id).
    """
    raw = config_manager.load_config(apply_active_profile=False)
    raw.ensure_servers_migrated()
    server = raw.get_server_by_id(server_id) if server_id else raw.get_admin_active_server()
    if not server:
        return raw, None
    raw.sync_active_profile_to_legacy(server)
    raw.emby_url = server.emby_url
    raw.emby_api_key = server.emby_api_key
    raw.emby_server_id = server.emby_server_id or raw.emby_server_id
    return raw, str(server.id)


def _extract_webhook_token(request: Request) -> str:
    incoming = (request.headers.get("X-Webhook-Secret") or "").strip()
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        incoming = incoming or auth[7:].strip()
    q_token = request.query_params.get("token")
    if q_token:
        incoming = incoming or q_token.strip()
    return incoming


def _validate_webhook_tokens(cfg: AppConfig) -> None:
    """Webhook token policy: enabled server must set non-empty unique token."""
    seen: dict[str, str] = {}
    for s in cfg.servers:
        sid = str(s.id)
        profile = cfg.get_server_profile(sid)
        wc = WebhookSettings.model_validate(profile.get("webhook") or {})
        if not wc.enabled:
            continue
        token = (wc.secret or "").strip()
        if not token:
            raise HTTPException(
                status_code=400,
                detail=f"Webhook 已启用但 token 为空：server={sid}",
            )
        owner = seen.get(token)
        if owner and owner != sid:
            raise HTTPException(
                status_code=400,
                detail=f"Webhook token 重复：server={owner} 与 server={sid}",
            )
        seen[token] = sid


def _resolve_server_id_by_webhook_token(cfg: AppConfig, token: str) -> Optional[str]:
    t = (token or "").strip()
    if not t:
        return None
    for s in cfg.servers:
        sid = str(s.id)
        profile = cfg.get_server_profile(sid)
        wc = WebhookSettings.model_validate(profile.get("webhook") or {})
        if not wc.enabled:
            continue
        if (wc.secret or "").strip() == t:
            return sid
    return None


def _scheduler_rebuild_signature(cfg: AppConfig) -> str:
    """
    Build a stable signature for scheduler-relevant settings across all servers.
    Active server switching alone should not change this signature.
    """
    cfg.ensure_servers_migrated()
    server_entries = []
    for s in sorted(cfg.servers, key=lambda x: str(x.id)):
        sid = str(s.id)
        profile = cfg.get_server_profile(sid) or {}
        profile_cache_h = profile.get("cache_refresh_interval")
        cron_expr = str(profile.get("real_library_cover_cron") or "").strip()
        libs = []
        for raw_lib in list(profile.get("library") or []):
            vlib_id = str(raw_lib.get("id") or "")
            interval = raw_lib.get("cache_refresh_interval")
            effective_h = interval if interval is not None else profile_cache_h
            if effective_h is None:
                effective_h = cfg.cache_refresh_interval
            try:
                effective_h = int(effective_h or 0)
            except Exception:
                effective_h = 0
            libs.append(
                {
                    "id": vlib_id,
                    "hidden": bool(raw_lib.get("hidden", False)),
                    "resource_type": str(raw_lib.get("resource_type") or ""),
                    "cache_refresh_interval": effective_h,
                }
            )
        libs.sort(key=lambda x: x["id"])
        server_entries.append(
            {
                "id": sid,
                "enabled": bool(getattr(s, "enabled", True)),
                "proxy_port": int(getattr(s, "proxy_port", 0) or 0),
                "real_library_cover_cron": cron_expr,
                "virtual_libraries": libs,
            }
        )
    return json.dumps(server_entries, sort_keys=True, ensure_ascii=True)

admin_app = FastAPI(title="Emby Virtual Proxy - Admin API")

# 认证中间件：在请求到达路由之前统一拦截
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse as StarletteJSONResponse

# 不需要认证的路径白名单
AUTH_PUBLIC_PATHS = {"/api/login", "/api/auth-status", "/api/logout", "/api/webhook/emby"}

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not AUTH_ENABLED:
            return await call_next(request)
        # 非 /api 开头的请求（静态文件、前端路由）不拦截
        if not request.url.path.startswith("/api"):
            return await call_next(request)
        # 白名单路径不拦截
        if request.url.path in AUTH_PUBLIC_PATHS:
            return await call_next(request)
        # 图片代理等前缀匹配的公开路径
        if request.url.path.startswith("/api/emby/image-proxy/"):
            return await call_next(request)
        # 验证 token
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if _verify_token(token):
                return await call_next(request)
        return StarletteJSONResponse(
            status_code=401,
            content={"detail": "未授权，请先登录"}
        )

admin_app.add_middleware(AuthMiddleware)

# 所有 API 路由（认证由中间件统一处理，不再需要 Depends）
public_router = APIRouter(prefix="/api")
api_router = APIRouter(prefix="/api")

# Define path for the library items DB, as it's not in db_manager
RSS_LIBRARY_ITEMS_DB = Path("/app/config/rss_library_items.db")

class CoverRequest(BaseModel):
    library_id: str
    title_zh: str # 之前是 library_name，现在改为 title_zh
    title_en: Optional[str] = None
    style_name: str # 新增：用于指定封面样式
    temp_image_paths: Optional[List[str]] = None

class LoginRequest(BaseModel):
    username: str
    password: str

# --- 认证 API（公开路由，不需要 token） ---
@public_router.get("/auth-status", tags=["Auth"])
async def auth_status():
    """返回当前认证是否启用"""
    return {"auth_enabled": AUTH_ENABLED}

@public_router.post("/login", tags=["Auth"])
async def login(body: LoginRequest):
    """用户登录，验证用户名密码并返回 token"""
    if not AUTH_ENABLED:
        return {"token": "", "message": "认证未启用"}
    if body.username == ADMIN_USERNAME and body.password == ADMIN_PASSWORD:
        _cleanup_expired_tokens()
        token = _generate_token()
        logger.info(f"User '{body.username}' logged in successfully.")
        return {"token": token}
    logger.warning(f"Failed login attempt for user '{body.username}'.")
    raise HTTPException(status_code=401, detail="用户名或密码错误")

@public_router.post("/logout", tags=["Auth"])
async def logout(request: Request):
    """登出，使当前 token 失效"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        _valid_tokens.pop(token, None)
    return {"message": "已登出"}


@public_router.post("/webhook/emby", tags=["Webhook"])
async def emby_webhook_receive(request: Request):
    """
    Emby Premiere Webhook 回调。POST 至：
    http(s)://<管理面板>/api/webhook/emby
    若设置了密钥，任选其一：请求头 X-Webhook-Secret、Authorization: Bearer、或查询参数 ?token=
    """
    config = config_manager.load_config(apply_active_profile=False)
    incoming = _extract_webhook_token(request)
    if not incoming:
        raise HTTPException(status_code=403, detail="Webhook token is required")
    sid = _resolve_server_id_by_webhook_token(config, incoming)
    if not sid:
        raise HTTPException(status_code=403, detail="Invalid webhook token")

    try:
        body = await request.json()
    except Exception:
        try:
            form = await request.form()
            body = {k: form.get(k) for k in form.keys()}
        except Exception:
            body = {}
    payload = parse_request_payload(body)
    logger.debug(f"Webhook RAW body: {body}")
    logger.debug(f"Webhook PARSED payload: {payload}")
    asyncio.create_task(_handle_emby_webhook_payload(payload, server_id=sid))
    return {"ok": True, "accepted": True, "server_id": sid}

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


async def _populate_vlib_cache(vlib: VirtualLibrary, config: AppConfig):
    """
    Simulate browsing a virtual library by querying Emby directly.
    Writes the fetched items into vlib_items_cache so cover generation can use them.
    """
    if not config.emby_url or not config.emby_api_key:
        logger.warning(f"Cannot populate cache for '{vlib.name}': Emby URL or API key not configured.")
        return

    headers = {'X-Emby-Token': config.emby_api_key, 'Accept': 'application/json'}

    # Get a reference user ID for the query
    users = await fetch_from_emby("/Users", config=config)
    if not users:
        logger.warning("Cannot populate cache: no Emby users found.")
        return
    user_id = users[0].get("Id")

    base_url = f"{config.emby_url.rstrip('/')}/emby/Users/{user_id}/Items"
    params = {
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Series,Video",
        "Fields": "ImageTags,ProviderIds,Genres,Tags,Studios,OfficialRatings,"
                  "CommunityRating,ProductionYear,VideoRange,Container,"
                  "ProductionLocations,DateLastMediaAdded,DateCreated,"
                  "BackdropImageTags,SortName,PremiereDate,CriticRating,"
                  "SeriesStatus,RunTimeTicks",
        "SortBy": "DateCreated",
        "SortOrder": "Descending",
    }

    # Apply resource type filter
    resource_map = {
        "collection": "CollectionIds", "tag": "TagIds",
        "person": "PersonIds", "genre": "GenreIds", "studio": "StudioIds"
    }
    res_ids = vlib.resolved_resource_ids() if vlib.resource_type in resource_map else []
    rp_name = resource_map.get(vlib.resource_type)
    if rp_name and len(res_ids) == 1:
        params[rp_name] = res_ids[0]
    elif rp_name and len(res_ids) == 0:
        logger.warning(f"Skipping cache population for '{vlib.name}': no resource ids.")
        return
    elif vlib.resource_type in ("rsshub", "random"):
        logger.info(f"Skipping cache population for '{vlib.resource_type}' library '{vlib.name}'.")
        return

    # Apply advanced filter if configured
    if vlib.advanced_filter_id:
        adv_filter = next((f for f in config.advanced_filters if f.id == vlib.advanced_filter_id), None)
        if adv_filter:
            from proxy_handlers._filter_translator import translate_rules

            emby_native_params, _ = translate_rules(adv_filter.rules)
            params.update(emby_native_params)

    # Determine source libraries
    ignore_set = config.disabled_library_ids
    source_libs = [lid for lid in (vlib.source_libraries or []) if lid not in ignore_set]

    all_items = []
    try:
        async with create_client_session() as session:
            async def _fetch_all_pages(fetch_params):
                """Fetch all pages from Emby."""
                items = []
                start = 0
                limit = 400
                while True:
                    p = dict(fetch_params)
                    p["StartIndex"] = str(start)
                    p["Limit"] = str(limit)
                    async with session.get(base_url, params=p, headers=headers, timeout=30) as resp:
                        if resp.status != 200:
                            break
                        data = await resp.json()
                        batch = data.get("Items", [])
                        if not batch:
                            break
                        items.extend(batch)
                        start += len(batch)
                        if len(batch) < limit:
                            break
                return items

            if rp_name and len(res_ids) > 1:
                if source_libs:
                    for pid in source_libs:
                        for rid in res_ids:
                            p = dict(params)
                            p["ParentId"] = pid
                            p[rp_name] = rid
                            all_items.extend(await _fetch_all_pages(p))
                else:
                    for rid in res_ids:
                        p = dict(params)
                        p[rp_name] = rid
                        all_items.extend(await _fetch_all_pages(p))
            elif source_libs:
                for pid in source_libs:
                    p = dict(params)
                    p["ParentId"] = pid
                    all_items.extend(await _fetch_all_pages(p))
            else:
                all_items = await _fetch_all_pages(params)
    except Exception as e:
        logger.error(f"Failed to populate cache for '{vlib.name}': {e}")
        return

    # Deduplicate
    seen = set()
    deduped = []
    for item in all_items:
        iid = item.get("Id")
        if iid and iid not in seen:
            seen.add(iid)
            deduped.append(item)

    # Match virtual library browse order: advanced filter custom sort (e.g. DateLastMediaAdded).
    # Without this, cover generation always used DateCreated order from the fetch, ignoring sort_field/sort_order.
    if vlib.advanced_filter_id and deduped:
        adv_filter = next((f for f in config.advanced_filters if f.id == vlib.advanced_filter_id), None)
        if adv_filter and adv_filter.sort_field and adv_filter.sort_order:
            from proxy_handlers.handler_items import _apply_custom_sort

            deduped = _apply_custom_sort(
                list(deduped), adv_filter.sort_field, adv_filter.sort_order
            )

    if deduped:
        active = config.get_admin_active_server()
        server_bucket = active.id if active else "default"
        vlib_items_cache.set_for_user(server_bucket, user_id, vlib.id, slim_items(deduped))
        logger.info(f"Populated on-disk cache for '{vlib.name}' user={user_id}: {len(deduped)} items (slimmed).")
    else:
        logger.warning(f"No items fetched for '{vlib.name}', cache not updated.")


def _list_rsshub_emby_item_ids(library_id: str, limit: int = 200) -> list[str]:
    """
    从 rss_library_items.db 读取 RSSHub 虚拟库中已匹配到 Emby 的条目 ID（按新到旧）。
    这些 ID 可直接用于 /emby/Items/{id}/Images/Primary 下载封面，不依赖 vlib_items_cache。
    """
    try:
        if not RSS_LIBRARY_ITEMS_DB.is_file():
            return []
        db = DBManager(RSS_LIBRARY_ITEMS_DB)
        rows = db.fetchall(
            """
            SELECT emby_item_id
            FROM rss_library_items
            WHERE library_id = ?
              AND emby_item_id IS NOT NULL
              AND TRIM(emby_item_id) <> ''
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (library_id, int(limit)),
        )
        ids: list[str] = []
        seen: set[str] = set()
        for row in rows:
            iid = str(row["emby_item_id"]).strip()
            if iid and iid not in seen:
                seen.add(iid)
                ids.append(iid)
        return ids
    except Exception as e:
        logger.warning("Read rss_library_items failed for vlib=%s: %s", library_id, e)
        return []


# 【【【 最终版本的 _fetch_images_from_vlib 函数 】】】
async def _fetch_images_from_vlib(
    library_id: str,
    temp_dir: Path,
    config: AppConfig,
    server_id: Optional[str] = None,
    cover_style: Optional[str] = None,
):
    """
    Read items from proxy cache via internal API and download cover images.
    Uses the first user who has on-disk cache for this vlib (not Emby /Users order).
    Random 虚拟库：从带主图的条目中随机抽最多 9 张，避免总用列表前几条导致刷新封面变化不大。

    style_shelf_1：与其它样式一致只取 9 条；`n.jpg`=Primary，`fanart_n.jpg`=Fanart→Backdrop（可无）；有 fanart_* 时从中选底图，**九槽皆无宽屏图时用槽 1 Primary 作底图**；底栏用 Primary（内容去重）。
    """
    scoped_cfg, resolved_sid = _load_server_scoped_config(server_id)
    vlib_meta = next((v for v in scoped_cfg.virtual_libraries if v.id == library_id), None)
    selected_items: list[dict]
    if vlib_meta and vlib_meta.resource_type == "rsshub":
        # RSSHub 数据不走 vlib_items_cache，而是保存在 rss_library_items.db。
        # 这里直接使用已匹配到 Emby 的 item id 作为封面素材来源。
        rss_item_ids = _list_rsshub_emby_item_ids(library_id)
        if not rss_item_ids:
            logger.warning(
                "COVER_FETCH failed: rsshub emby ids empty server_id=%s vlib=%s",
                resolved_sid,
                library_id,
            )
            raise HTTPException(
                status_code=404,
                detail="No matched Emby items found for this RSS library. Please refresh RSS data first.",
            )
        selected_ids = rss_item_ids[:9]
        selected_items = [{"Id": iid} for iid in selected_ids]
        logger.info(
            "COVER_FETCH rsshub source server_id=%s vlib=%s candidates=%s selected=%s",
            resolved_sid,
            library_id,
            len(rss_item_ids),
            len(selected_items),
        )
    else:
        cache_user_ids = await _list_vlib_cache_user_ids_from_proxy(library_id, server_id=resolved_sid)
        cache_uid = await _resolve_cover_cache_user_id(library_id, server_id=resolved_sid)
        if not cache_uid:
            logger.warning(
                "COVER_FETCH failed: no cache user resolved "
                "server_id=%s vlib=%s cache_user_ids=%s",
                resolved_sid,
                library_id,
                cache_user_ids,
            )
            raise HTTPException(
                status_code=404,
                detail="No cached items found for this virtual library. Please refresh data first.",
            )
        logger.info(
            "COVER_FETCH start server_id=%s vlib=%s hit_user_id=%s cache_user_ids=%s emby=%s",
            resolved_sid,
            library_id,
            cache_uid,
            cache_user_ids,
            (scoped_cfg.emby_url or "").rstrip("/"),
        )

        items = await _get_cached_items_from_proxy(library_id, user_id=cache_uid, server_id=resolved_sid)
        if not items:
            logger.warning(
                "COVER_FETCH failed: cached items empty "
                "server_id=%s vlib=%s hit_user_id=%s cache_user_ids=%s",
                resolved_sid,
                library_id,
                cache_uid,
                cache_user_ids,
            )
            raise HTTPException(status_code=404, detail="No cached items found for this virtual library. Please refresh data first.")

        items_with_images = [item for item in items if item.get("ImageTags", {}).get("Primary")]
        if not items_with_images:
            logger.warning(
                "COVER_FETCH failed: cached items without primary images "
                "server_id=%s vlib=%s hit_user_id=%s cache_user_ids=%s items=%s",
                resolved_sid,
                library_id,
                cache_uid,
                cache_user_ids,
                len(items),
            )
            raise HTTPException(status_code=404, detail="Cached items contain no items with cover images.")

        if vlib_meta and vlib_meta.resource_type == "random":
            k = min(9, len(items_with_images))
            selected_items = random.sample(items_with_images, k)
        else:
            selected_items = items_with_images[:9]

    async with create_client_session() as session:
        ok = await download_cover_images_emby(
            session,
            scoped_cfg.emby_url or "",
            scoped_cfg.emby_api_key or "",
            selected_items,
            temp_dir,
            style_shelf_1=(cover_style == "style_shelf_1"),
        )

    if not ok:
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

async def _admin_emby_get_json(
    endpoint: str, params: Optional[Dict] = None, config: Optional[AppConfig] = None
) -> Optional[dict]:
    """GET Emby JSON，失败返回 None（不抛 HTTPException）。"""
    scoped = config or config_manager.load_config()
    if not scoped.emby_url or not scoped.emby_api_key:
        return None
    headers = {"X-Emby-Token": scoped.emby_api_key, "Accept": "application/json"}
    url = f"{scoped.emby_url.rstrip('/')}/emby{endpoint}"
    try:
        async with create_client_session() as session:
            async with session.get(url, headers=headers, params=params or {}, timeout=20) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception as e:
        logger.warning(f"Emby GET {endpoint} failed: {e}")
        return None


async def _fetch_latest_images_for_real_library(
    real_lib_id: str,
    temp_dir: Path,
    config: AppConfig,
    cover_style: Optional[str] = None,
):
    """从真实库中获取最新入库的媒体封面图片（按 DateCreated 降序）。"""
    if not config.emby_url or not config.emby_api_key:
        logger.warning("Cannot fetch images: Emby URL or API key not configured.")
        return

    headers = {'X-Emby-Token': config.emby_api_key, 'Accept': 'application/json'}
    users = await fetch_from_emby("/Users", config=config)
    if not users:
        return
    user_id = users[0].get("Id")

    base_url = f"{config.emby_url.rstrip('/')}/emby/Users/{user_id}/Items"
    params = {
        "ParentId": real_lib_id,
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Series,Video",
        "Fields": "ImageTags,DateCreated",
        "Limit": "9",
        "SortBy": "DateCreated",
        "SortOrder": "Descending",
    }

    try:
        async with create_client_session() as session:
            async with session.get(base_url, params=params, headers=headers, timeout=30) as resp:
                if resp.status != 200:
                    logger.warning(f"Fetch latest items for real lib {real_lib_id} failed: {resp.status}")
                    return
                data = await resp.json()
                items = data.get("Items", [])
    except Exception as e:
        logger.error(f"Failed to fetch latest items for real lib {real_lib_id}: {e}")
        return

    items_with_images = [item for item in items if item.get("ImageTags", {}).get("Primary")]
    if not items_with_images:
        logger.warning(f"No items with cover images in real lib {real_lib_id}")
        return

    selected_items = items_with_images[:9]
    async with create_client_session() as session:
        ok = await download_cover_images_emby(
            session,
            config.emby_url or "",
            config.emby_api_key or "",
            selected_items,
            temp_dir,
            style_shelf_1=(cover_style == "style_shelf_1"),
        )
    if not ok:
        logger.warning(f"Real lib {real_lib_id}: no cover images downloaded (style={cover_style!r})")


async def _upload_image_to_emby(item_id: str, image_bytes: bytes, config: AppConfig):
    """通过 Emby API 上传图片到指定 Item，使直连 Emby 也能看到自定义封面。"""
    if not config.emby_url or not config.emby_api_key:
        logger.warning("Cannot upload image to Emby: URL or API key not configured.")
        return
    url = f"{config.emby_url.rstrip('/')}/emby/Items/{item_id}/Images/Primary"
    image_b64 = base64.b64encode(image_bytes).decode('ascii')
    headers = {
        'X-Emby-Token': config.emby_api_key,
        'Content-Type': 'image/jpeg',
    }
    try:
        async with create_client_session() as session:
            async with session.post(url, data=image_b64, headers=headers, timeout=30) as resp:
                if resp.status in (200, 204):
                    logger.info(f"Uploaded cover to Emby for item {item_id}")
                else:
                    text = await resp.text()
                    logger.warning(f"Emby image upload failed for {item_id}: {resp.status} {text}")
    except Exception as e:
        logger.error(f"Failed to upload image to Emby for {item_id}: {e}")


async def _generate_real_library_cover(rl: RealLibraryConfig, config: AppConfig) -> Optional[str]:
    """为真实库生成封面，复用虚拟库封面生成逻辑。"""
    title_zh = rl.cover_title_zh or rl.name
    title_en = rl.cover_title_en or ""
    style_name = config.default_cover_style

    FONT_DIR = "/app/src/assets/fonts/"
    OUTPUT_DIR = "/app/config/images/"
    Path(OUTPUT_DIR).mkdir(exist_ok=True)
    image_gen_dir = Path(OUTPUT_DIR) / f"temp_{str(uuid.uuid4())}"
    image_gen_dir.mkdir()

    try:
        if config.custom_image_path:
            await _fetch_images_from_custom_path(config.custom_image_path, image_gen_dir)
        else:
            await _fetch_latest_images_for_real_library(rl.id, image_gen_dir, config, cover_style=style_name)

        zh_font_path = config.custom_zh_font_path if config.custom_zh_font_path else os.path.join(FONT_DIR, "multi_1_zh.ttf")
        en_font_path = config.custom_en_font_path if config.custom_en_font_path else os.path.join(FONT_DIR, "multi_1_en.ttf")

        import cover_subprocess

        if style_name not in cover_subprocess.ALLOWED_COVER_STYLES:
            logger.warning(f"Unknown cover style: {style_name}")
            return None

        if style_name in ('style_single_1', 'style_single_2'):
            main_image_path = image_gen_dir / "1.jpg"
            if not main_image_path.is_file():
                logger.warning(f"Real lib cover: no main image for {rl.name}")
                return None

        output_path = os.path.join(OUTPUT_DIR, f"{rl.id}.jpg")
        job = {
            "style_name": style_name,
            "title_zh": title_zh,
            "title_en": title_en,
            "zh_font_path": zh_font_path,
            "en_font_path": en_font_path,
            "library_dir": str(image_gen_dir),
            "output_path": output_path,
            "crop_16_9": True,
        }
        try:
            await cover_subprocess.run_cover_worker_job(job)
        except RuntimeError as e:
            logger.error(f"Failed to generate cover for real lib '{rl.name}': {e}")
            return None

        image_tag = hashlib.md5(str(time.time()).encode()).hexdigest()
        logger.info(f"Real lib cover saved: {output_path}, tag={image_tag}")

        with open(output_path, "rb") as f:
            await _upload_image_to_emby(rl.id, f.read(), config)

        return image_tag
    except Exception as e:
        logger.error(f"Failed to generate cover for real lib '{rl.name}': {e}", exc_info=True)
        return None
    finally:
        shutil.rmtree(image_gen_dir, ignore_errors=True)


async def refresh_all_real_library_covers(server_id: Optional[str] = None):
    """定时任务：刷新所有启用且开启封面生成的真实库封面。"""
    config, resolved_sid = _load_server_scoped_config(server_id)
    for rl in config.real_libraries:
        if not rl.enabled or not rl.cover_enabled:
            continue
        logger.info(f"Cron: refreshing cover for real lib '{rl.name}' ({rl.id})")
        tag = await _generate_real_library_cover(rl, config)
        if tag:
            # 更新该 server profile 中的 real_libraries.image_tag
            current_config = config_manager.load_config(apply_active_profile=False)
            sid = resolved_sid or str(config.admin_active_server_id or "")
            profile = current_config.get_server_profile(sid) if sid else {}
            libs = list(profile.get("real_libraries") or [])
            for r in libs:
                if str(r.get("id")) == str(rl.id):
                    r["image_tag"] = tag
                    break
            profile["real_libraries"] = libs
            if sid:
                current_config.set_server_profile(sid, profile)
                config_manager.save_config(current_config, sync_active_profile=False)


async def refresh_virtual_library_data_and_cover(vlib: VirtualLibrary, server_id: Optional[str] = None) -> None:
    """与「刷新数据」一致：清缓存、RSS 则拉条目，再重生封面。

    random 库条目按用户随机，封面仍用 _resolve_cover_cache_user_id 对应用户的缓存素材合成，
    可能与其它用户看到的列表不一致，但保留刷新封面以便更新展示图。
    """
    library_id = vlib.id
    logger.info(
        f"VLIB_REFRESH START vlib={library_id} name='{vlib.name}' "
        f"type={vlib.resource_type} interval_h={vlib.cache_refresh_interval}"
    )
    keys_to_remove = [k for k in api_cache if library_id in str(k)]
    for k in keys_to_remove:
        api_cache.pop(k, None)
    inv = await _notify_proxy_invalidate_cache(library_id, server_id=server_id)
    had_users = list(inv.get("user_ids") or [])

    logger.info(
        f"VLIB_REFRESH CACHE_CLEARED vlib={library_id} "
        f"api_cache={len(keys_to_remove)} prior_cached_users={len(had_users)}"
    )

    if vlib.resource_type == "rsshub":
        await enqueue_rss_refresh(vlib, manual=False, wait_for_completion=True)
    else:
        # 默认对「此前已有磁盘缓存」的用户重拉；若是新库无人缓存，则回退用首个 Emby 用户预热一次，
        # 这样封面生成不再因为“无缓存素材”而报 404。
        target_users = had_users
        if not target_users:
            fallback_uid = await _resolve_cover_cache_user_id(library_id, server_id=server_id)
            if fallback_uid:
                target_users = [fallback_uid]
        await _notify_proxy_refresh_cache(library_id, user_ids=target_users, server_id=server_id)

    await _regenerate_cover_for_vlib(vlib, server_id=server_id)
    logger.info(f"VLIB_REFRESH DONE vlib={library_id} name='{vlib.name}' type={vlib.resource_type}")



async def _handle_emby_webhook_payload(payload: Dict[str, Any], server_id: str) -> None:
    config, sid = _load_server_scoped_config(server_id)
    if not sid:
        return
    wc = config.webhook
    if not wc.enabled:
        return

    event_raw = extract_event_raw(payload)
    if event_raw not in SUPPORTED_EVENTS:
        logger.debug(f"Webhook: 忽略事件类型 '{event_raw}'")
        return

    item = extract_item_dict(payload)
    item_path = item.get("Path") or ""
    if not item_path:
        logger.warning(f"Webhook: 事件 '{event_raw}' 缺少 Item.Path，跳过")
        return

    # 通过 Emby VirtualFolders API 获取每个库的实际路径，用路径前缀匹配 item path
    try:
        vfolders = await _admin_emby_get_json("/Library/VirtualFolders", config=config)
    except Exception as e:
        logger.error(f"Webhook: 获取 VirtualFolders 失败: {e}")
        return

    if not vfolders:
        logger.warning("Webhook: VirtualFolders 返回为空，跳过")
        return

    # vfolders 是一个列表，每项包含 Name, ItemId, Locations 等
    matched_lib_ids: List[str] = []
    for vf in vfolders:
        lib_id = vf.get("ItemId") or ""
        locations = vf.get("Locations") or []
        for loc in locations:
            # 统一去掉尾部斜杠后做前缀匹配
            loc_normalized = loc.rstrip("/")
            if item_path.startswith(loc_normalized + "/") or item_path == loc_normalized:
                matched_lib_ids.append(lib_id)
                break  # 一个库匹配到即可，不用重复添加

    if not matched_lib_ids:
        logger.info(f"Webhook: event={event_raw} path='{item_path}' 未匹配到任何真实库")
        return

    # 合并到待处理集合
    pending = _webhook_pending_lib_ids_by_server.setdefault(sid, set())
    pending.update(matched_lib_ids)
    delay = max(wc.delay_seconds, 0)
    logger.info(
        f"Webhook: server={sid} event={event_raw} path='{item_path}' → 匹配真实库 {matched_lib_ids}"
        f"（待处理共 {len(pending)} 个，{delay}s 后刷新）"
    )

    # 只在没有待执行任务时启动定时器，避免不断重置导致饥饿
    t = _webhook_pending_task_by_server.get(sid)
    if t is None or t.done():
        _webhook_pending_task_by_server[sid] = asyncio.create_task(
            _webhook_delayed_flush(server_id=sid, delay_seconds=delay)
        )


async def _webhook_delayed_flush(server_id: str, delay_seconds: int):
    """延迟后统一刷新所有待处理的真实库关联的虚拟库。"""
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)

    # 取出并清空待处理集合
    pending = _webhook_pending_lib_ids_by_server.get(server_id, set())
    lib_ids = list(pending)
    pending.clear()
    _webhook_pending_task_by_server.pop(server_id, None)

    if not lib_ids:
        return

    config, sid = _load_server_scoped_config(server_id)
    if not sid:
        return
    ignore_set = config.disabled_library_ids
    refreshed = set()
    for v in config.virtual_libraries:
        if v.hidden or v.resource_type in ("rsshub", "random") or v.resource_type != "all":
            continue
        src = v.source_libraries or []
        if not src:
            continue
        src_filtered = [x for x in src if x not in ignore_set]
        if any(lid in src_filtered for lid in lib_ids):
            if v.id not in refreshed:
                refreshed.add(v.id)
                logger.info(
                    f"Webhook flush: server={sid} 刷新虚拟库 '{v.name}' ({v.id}) ← 真实库 {lib_ids}"
                )
                asyncio.create_task(refresh_virtual_library_data_and_cover(v, server_id=sid))

    if not refreshed:
        logger.info(f"Webhook flush: server={sid} 真实库 {lib_ids} 无关联虚拟库需要刷新")


# --- API ---
@api_router.get("/config", tags=["Configuration"])
async def get_config():
    # Return global + servers metadata/profile containers; do NOT project active profile
    # into top-level legacy fields to avoid frontend confusion.
    cfg = config_manager.load_config(apply_active_profile=False)
    return cfg.model_dump(by_alias=True)

# 修改 update_config
@api_router.post("/config", tags=["Configuration"])
async def update_config(config: AppConfig):
    prev_cfg = config_manager.load_config(apply_active_profile=False)
    prev_scheduler_sig = _scheduler_rebuild_signature(prev_cfg)
    prev_server_ids = {str(s.id) for s in prev_cfg.servers}

    config.ensure_servers_migrated()
    _validate_webhook_tokens(config)
    # Basic validation: proxy ports must be unique for enabled servers.
    used_ports = set()
    for s in config.servers:
        if not s.enabled:
            continue
        p = int(s.proxy_port)
        if p in used_ports:
            raise HTTPException(status_code=400, detail=f"重复的代理端口: {p}")
        used_ports.add(p)
    if config.admin_active_server_id and not any(s.id == config.admin_active_server_id for s in config.servers):
        config.admin_active_server_id = config.servers[0].id if config.servers else None

    new_server_ids = {str(s.id) for s in config.servers}
    removed_server_ids = sorted(prev_server_ids - new_server_ids)
    for sid in removed_server_ids:
        _remove_scheduler_jobs_for_server(sid)

    # /config is treated as global + servers metadata update; do not overwrite
    # active server profile from top-level legacy fields.
    config_manager.save_config(config, sync_active_profile=False)
    saved_raw = config_manager.load_config(apply_active_profile=False)
    new_scheduler_sig = _scheduler_rebuild_signature(saved_raw)
    # Re-load saved config so return value + schedulers match on-disk state
    saved = config_manager.load_config()
    if not saved.enable_cache:
        api_cache.clear()
        logger.info("enable_cache=false: cleared api_cache")
    # 仅在调度相关配置发生变化时重建任务，避免仅切换 active server 造成重复重建。
    if prev_scheduler_sig != new_scheduler_sig:
        update_virtual_library_refresh_jobs(saved)
        update_real_library_cover_cron(saved)
    else:
        logger.info("Skip scheduler rebuild: no scheduler-related config changes.")
    return config_manager.load_config(apply_active_profile=False).model_dump(by_alias=True)


@api_router.get("/servers/{server_id}/profile", tags=["Configuration"])
async def get_server_profile(server_id: str):
    cfg = config_manager.load_config(apply_active_profile=False)
    server = cfg.get_server_by_id(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return cfg.get_server_profile(server_id)


@api_router.put("/servers/{server_id}/profile", tags=["Configuration"])
async def update_server_profile(server_id: str, profile: Dict[str, Any]):
    cfg = config_manager.load_config(apply_active_profile=False)
    if not cfg.set_server_profile(server_id, profile):
        raise HTTPException(status_code=404, detail="Server not found")
    _validate_webhook_tokens(cfg)
    config_manager.save_config(cfg, sync_active_profile=False)
    # Schedulers depend on active top-level projection; re-load with projection for safety.
    saved = config_manager.load_config()
    update_virtual_library_refresh_jobs(saved)
    update_real_library_cover_cron(saved)
    return cfg.get_server_profile(server_id)

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
        import docker
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
        logger.info(
            f"RSS_REFRESH START vlib={vlib.id} name='{vlib.name}' "
            f"type={vlib.rss_type} interval_h={vlib.cache_refresh_interval}"
        )
        # page cache removed in refactor
        keys_to_remove = [k for k in api_cache if vlib.id in str(k)]
        for k in keys_to_remove:
            api_cache.pop(k, None)
        if vlib.rss_type in ("douban", "bangumi"):
            await run_rss_refresh_job(vlib)
        else:
            logger.warning(f"Unknown RSS type: {vlib.rss_type} for library {vlib.id}")
        logger.info(f"RSS_REFRESH DONE vlib={vlib.id} name='{vlib.name}'")
    except Exception as e:
        logger.error(f"Error refreshing RSS library {vlib.id}: {e}", exc_info=True)


# --- RSS 刷新全局串行队列：同一时间只跑一个子进程任务；手动请求在已有排队/执行时返回 409 ---
_rss_refresh_queue: asyncio.Queue[
    tuple[VirtualLibrary, Optional[asyncio.Future[None]]]
] = asyncio.Queue()
_rss_refresh_processing: bool = False
_rss_refresh_worker_task: Optional[asyncio.Task[None]] = None
_rss_state_lock = asyncio.Lock()


def _ensure_rss_refresh_worker() -> None:
    global _rss_refresh_worker_task
    if _rss_refresh_worker_task is None or _rss_refresh_worker_task.done():
        _rss_refresh_worker_task = asyncio.create_task(
            _rss_refresh_worker_loop(),
            name="rss_refresh_worker",
        )


async def _rss_refresh_worker_loop() -> None:
    global _rss_refresh_processing
    while True:
        vlib, fut = await _rss_refresh_queue.get()
        _rss_refresh_processing = True
        try:
            await refresh_rss_library_internal(vlib)
            if fut is not None and not fut.done():
                fut.set_result(None)
        except Exception as e:
            if fut is not None and not fut.done():
                fut.set_exception(e)
            logger.error("RSS queue worker: %s", e, exc_info=True)
        finally:
            _rss_refresh_processing = False
            _rss_refresh_queue.task_done()


async def enqueue_rss_refresh(
    vlib: VirtualLibrary,
    *,
    manual: bool = False,
    wait_for_completion: bool = False,
) -> None:
    """
    将 RSS 刷新加入全局队列，FIFO 串行执行。
    manual=True：若当前正在执行或队列非空则 HTTP 409（仅用于「仅 RSS 刷新」接口）。
    wait_for_completion=True：阻塞直到该条任务执行完毕（如「刷新数据」需先 RSS 再封面）。
    """
    _ensure_rss_refresh_worker()
    fut: Optional[asyncio.Future[None]] = asyncio.Future() if wait_for_completion else None
    if manual:
        async with _rss_state_lock:
            if _rss_refresh_processing or _rss_refresh_queue.qsize() > 0:
                raise HTTPException(
                    status_code=409,
                    detail="已有 RSS 刷新任务在执行或排队，请稍后再试。",
                )
            await _rss_refresh_queue.put((vlib, fut))
    else:
        await _rss_refresh_queue.put((vlib, fut))
    if fut is not None:
        await fut


@api_router.post("/libraries/{library_id}/refresh", status_code=202, tags=["Libraries"])
async def refresh_rss_library(library_id: str, request: Request):
    """手动触发 RSS 虚拟库的刷新"""
    config = config_manager.load_config()
    vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)

    if not vlib:
        raise HTTPException(status_code=404, detail="Virtual library not found")
    if vlib.resource_type != "rsshub":
        raise HTTPException(status_code=400, detail="This library is not an RSSHUB library")

    await enqueue_rss_refresh(vlib, manual=True, wait_for_completion=False)

    return {"message": "RSS library refresh process has been started."}


async def _regenerate_cover_for_vlib(vlib: VirtualLibrary, server_id: Optional[str] = None):
    """
    仅重生成封面图：不触发虚拟库列表缓存刷新（避免 random 等库只更新「代表用户」、其它用户推荐仍旧）。
    素材来自现有磁盘缓存（_resolve_cover_cache_user_id）；无缓存时需先「刷新数据」。
    """
    config, resolved_sid = _load_server_scoped_config(server_id)
    style_name = config.default_cover_style
    title_zh = vlib.cover_title_zh or vlib.name
    title_en = vlib.cover_title_en or ""
    try:
        image_tag = await _generate_library_cover(vlib.id, title_zh, title_en, style_name, server_id=resolved_sid)
        if image_tag:
            current_config = config_manager.load_config(apply_active_profile=False)
            sid = resolved_sid or str(config.admin_active_server_id or "")
            profile = current_config.get_server_profile(sid) if sid else {}
            libs = list(profile.get("library") or [])
            for v in libs:
                if str(v.get("id")) == str(vlib.id):
                    v["image_tag"] = image_tag
                    break
            profile["library"] = libs
            if sid:
                current_config.set_server_profile(sid, profile)
                config_manager.save_config(current_config, sync_active_profile=False)
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
    cfg = config_manager.load_config(apply_active_profile=False)
    sid = str(cfg.admin_active_server_id or "")
    asyncio.create_task(_regenerate_cover_for_vlib(vlib, server_id=sid))
    return {"message": f"Cover refresh started for '{vlib.name}'."}


@api_router.post("/libraries/{library_id}/refresh-data", status_code=202, tags=["Libraries"])
async def refresh_library_data(library_id: str):
    """Refresh virtual library item cache, then regenerate its cover (random: cover uses one cache user’s items)."""
    logger.info(f"API refresh_library_data called for library_id={library_id}")
    config = config_manager.load_config()
    raw = config_manager.load_config(apply_active_profile=False)
    sid = str(raw.admin_active_server_id or "")
    vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)
    resolved_sid = sid
    if not vlib:
        # Fallback: search all server profiles to avoid false 404 caused by context lag.
        for s in raw.servers:
            p = raw.get_server_profile(str(s.id))
            libs = p.get("library") or []
            found = next((x for x in libs if str(x.get("id")) == str(library_id)), None)
            if found:
                vlib = VirtualLibrary.model_validate(found)
                resolved_sid = str(s.id)
                break
    if not vlib:
        logger.warning(f"refresh_library_data: 虚拟库 {library_id} 不存在")
        raise HTTPException(status_code=404, detail="Virtual library not found")

    logger.info(f"refresh_library_data: 开始刷新虚拟库 '{vlib.name}' ({vlib.id}) type={vlib.resource_type}")
    asyncio.create_task(refresh_virtual_library_data_and_cover(vlib, server_id=resolved_sid))
    return {"message": f"Data and cover refresh started for '{vlib.name}'."}


@api_router.post("/covers/refresh-all", status_code=202, tags=["Cover Generator"])
async def refresh_all_covers():
    """Regenerate covers for all virtual libraries."""
    config = config_manager.load_config()
    vlibs = config.virtual_libraries

    async def _refresh_all():
        cfg = config_manager.load_config(apply_active_profile=False)
        sid = str(cfg.admin_active_server_id or "")
        for vlib in vlibs:
            await _regenerate_cover_for_vlib(vlib, server_id=sid)
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
async def _generate_library_cover(
    library_id: str,
    title_zh: str,
    title_en: Optional[str],
    style_name: str,
    temp_image_paths: Optional[List[str]] = None,
    server_id: Optional[str] = None,
) -> Optional[str]:
    config, resolved_sid = _load_server_scoped_config(server_id)
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
                await _fetch_images_from_vlib(
                    library_id,
                    image_gen_dir,
                    config,
                    resolved_sid,
                    cover_style=style_name,
                )

        # --- 3. 【核心改动】: 动态调用所选的样式生成函数 ---
        logger.info(f"素材准备完毕，开始使用样式 '{style_name}' 为 '{title_zh}' ({library_id}) 生成封面...")

        # --- 字体选择逻辑 ---
        vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)
        
        # 确定中文字体
        if vlib and vlib.cover_custom_zh_font_path:
            zh_font_path = vlib.cover_custom_zh_font_path
        elif config.custom_zh_font_path:
            zh_font_path = config.custom_zh_font_path
        else:
            zh_font_path = os.path.join(FONT_DIR, "multi_1_zh.ttf")

        # 确定英文字体
        if vlib and vlib.cover_custom_en_font_path:
            en_font_path = vlib.cover_custom_en_font_path
        elif config.custom_en_font_path:
            en_font_path = config.custom_en_font_path
        else:
            en_font_path = os.path.join(FONT_DIR, "multi_1_en.ttf")

        if style_name in ['style_single_1', 'style_single_2']:
            main_image_path = image_gen_dir / "1.jpg"
            if not main_image_path.is_file():
                raise HTTPException(status_code=404, detail="无法找到用于单图模式的主素材图片 (1.jpg)。")
        elif style_name == "style_shelf_1":
            bg_ok = any((image_gen_dir / f"1{ext}").is_file() for ext in (".jpg", ".jpeg", ".png", ".webp"))
            if not bg_ok:
                raise HTTPException(
                    status_code=404,
                    detail="无法找到用于背景+底栏样式的首图 (1.jpg / 1.png 等)。",
                )
        elif style_name != 'style_multi_1':
            raise HTTPException(status_code=400, detail=f"未知的样式名称: {style_name}")

        final_filename = f"{library_id}.jpg"
        output_path = os.path.join(OUTPUT_DIR, final_filename)
        job = {
            "style_name": style_name,
            "title_zh": title_zh,
            "title_en": title_en or "",
            "zh_font_path": zh_font_path,
            "en_font_path": en_font_path,
            "library_dir": str(image_gen_dir),
            "output_path": output_path,
            "crop_16_9": False,
        }
        try:
            import cover_subprocess

            await cover_subprocess.run_cover_worker_job(job)
        except RuntimeError as e:
            logger.error(f"封面生成子进程失败: {e}")
            raise HTTPException(status_code=500, detail="封面生成失败，请稍后重试或查看服务端日志。")

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
    scoped, _sid = _load_server_scoped_config()
    try:
        real_libs_from_emby = await get_real_libraries_hybrid_mode(config=scoped)
        for lib in real_libs_from_emby:
            all_libs.append({
                "id": lib.get("Id"), "name": lib.get("Name"), "type": "real",
                "collectionType": lib.get("CollectionType")
            })
    except HTTPException as e:
        raise e

    for lib in scoped.virtual_libraries:
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
    raw = config_manager.load_config(apply_active_profile=False)
    sid = str(raw.admin_active_server_id or "")
    if not sid:
        raise HTTPException(status_code=400, detail="No active server selected")
    profile = raw.get_server_profile(sid)
    libs_raw = list(profile.get("library") or [])
    display_order = list(profile.get("display_order") or [])

    library.id = str(uuid.uuid4())
    libs_raw.append(library.model_dump())
    if library.id not in display_order:
        display_order.append(library.id)
    profile["library"] = libs_raw
    profile["display_order"] = display_order
    raw.set_server_profile(sid, profile)
    config_manager.save_config(raw, sync_active_profile=False)
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
            
    # Persist to current active server profile explicitly (avoid projection mismatch)
    raw = config_manager.load_config(apply_active_profile=False)
    sid = str(raw.admin_active_server_id or "")
    if sid:
        profile = raw.get_server_profile(sid)
        profile["library"] = [v.model_dump() for v in config.virtual_libraries]
        profile["display_order"] = list(config.display_order or [])
        raw.set_server_profile(sid, profile)
        config_manager.save_config(raw, sync_active_profile=False)
    else:
        config_manager.save_config(config)
    return updated_lib

@api_router.patch("/libraries/{library_id}/toggle-hidden", tags=["Libraries"])
async def toggle_library_hidden(library_id: str):
    """切换虚拟库的隐藏/显示状态"""
    config = config_manager.load_config()
    for lib in config.virtual_libraries:
        if lib.id == library_id:
            lib.hidden = not lib.hidden
            raw = config_manager.load_config(apply_active_profile=False)
            sid = str(raw.admin_active_server_id or "")
            if sid:
                profile = raw.get_server_profile(sid)
                profile["library"] = [v.model_dump() for v in config.virtual_libraries]
                profile["display_order"] = list(config.display_order or [])
                raw.set_server_profile(sid, profile)
                config_manager.save_config(raw, sync_active_profile=False)
            else:
                config_manager.save_config(config)
            # 避免 api_cache 仍返回隐藏前的列表
            keys_to_remove = [k for k in api_cache if library_id in str(k)]
            for k in keys_to_remove:
                api_cache.pop(k, None)
            await _notify_proxy_invalidate_cache(library_id)

            return {"id": lib.id, "hidden": lib.hidden}

    raise HTTPException(status_code=404, detail="Virtual library not found")

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
    await _notify_proxy_invalidate_cache(library_id)

    raw = config_manager.load_config(apply_active_profile=False)
    sid = str(raw.admin_active_server_id or "")
    if sid:
        profile = raw.get_server_profile(sid)
        profile["library"] = [v.model_dump() for v in config.virtual_libraries]
        profile["display_order"] = list(config.display_order or [])
        raw.set_server_profile(sid, profile)
        config_manager.save_config(raw, sync_active_profile=False)
    else:
        config_manager.save_config(config)
    return Response(status_code=204)

# --- Real Library Management ---

@api_router.get("/real-libraries/sync", tags=["Real Libraries"])
async def sync_real_libraries():
    """从 Emby 同步真实库列表，合并到配置中（保留已有配置，新增默认启用）。"""
    scoped, sid = _load_server_scoped_config()
    if not sid:
        raise HTTPException(status_code=400, detail="No active server selected")
    emby_libs = await get_real_libraries_hybrid_mode(config=scoped)

    existing_map = {rl.id: rl for rl in scoped.real_libraries}
    synced: List[RealLibraryConfig] = []

    for lib in emby_libs:
        lib_id = lib.get("Id")
        lib_name = lib.get("Name", "")
        if lib_id in existing_map:
            rl = existing_map[lib_id]
            rl.name = lib_name  # 同步最新名称
            synced.append(rl)
        else:
            synced.append(RealLibraryConfig(id=lib_id, name=lib_name, enabled=True))

    raw = config_manager.load_config(apply_active_profile=False)
    profile = raw.get_server_profile(sid)
    profile["real_libraries"] = [rl.model_dump() for rl in synced]
    raw.set_server_profile(sid, profile)
    config_manager.save_config(raw, sync_active_profile=False)
    logger.info(f"Synced {len(synced)} real libraries from Emby. server={sid}")
    return [rl.model_dump() for rl in synced]


class SaveRealLibrariesRequest(BaseModel):
    libraries: List[RealLibraryConfig]
    cover_cron: Optional[str] = None

@api_router.post("/real-libraries", tags=["Real Libraries"])
async def save_real_libraries(body: SaveRealLibrariesRequest):
    """保存真实库配置列表及封面 cron。"""
    raw = config_manager.load_config(apply_active_profile=False)
    sid = str(raw.admin_active_server_id or "")
    if not sid:
        raise HTTPException(status_code=400, detail="No active server selected")
    profile = raw.get_server_profile(sid)
    profile["real_libraries"] = [rl.model_dump() for rl in body.libraries]
    profile["real_library_cover_cron"] = body.cover_cron
    raw.set_server_profile(sid, profile)
    config_manager.save_config(raw, sync_active_profile=False)
    update_real_library_cover_cron(config_manager.load_config())
    return {"ok": True}


@api_router.post("/real-libraries/{library_id}/refresh-cover", tags=["Real Libraries"])
async def refresh_real_library_cover(library_id: str):
    """手动刷新单个真实库封面。"""
    config, sid = _load_server_scoped_config()
    rl = next((r for r in config.real_libraries if r.id == library_id), None)
    if not rl:
        raise HTTPException(status_code=404, detail="Real library config not found")

    logger.info(f"Manual refresh cover for real lib '{rl.name}' ({rl.id})")
    tag = await _generate_real_library_cover(rl, config)
    if tag:
        raw = config_manager.load_config(apply_active_profile=False)
        profile = raw.get_server_profile(str(sid or ""))
        libs = list(profile.get("real_libraries") or [])
        for r in libs:
            if str(r.get("id")) == str(library_id):
                r["image_tag"] = tag
                break
        profile["real_libraries"] = libs
        if sid:
            raw.set_server_profile(str(sid), profile)
            config_manager.save_config(raw, sync_active_profile=False)
        return {"ok": True, "image_tag": tag}
    return {"ok": False, "detail": "Cover generation failed"}


@api_router.post("/real-libraries/refresh-all-covers", tags=["Real Libraries"])
async def refresh_all_real_library_covers_api():
    """手动刷新所有启用的真实库封面。"""
    raw = config_manager.load_config(apply_active_profile=False)
    sid = str(raw.admin_active_server_id or "")
    asyncio.create_task(refresh_all_real_library_covers(server_id=sid))
    return {"ok": True, "message": "All real library cover refresh started."}


@api_router.get("/emby/image-proxy/{item_id}", tags=["Emby Helper"])
async def proxy_emby_image(item_id: str):
    """代理 Emby 项目封面图片，避免前端跨域问题。"""
    config, _sid = _load_server_scoped_config()
    if not config.emby_url or not config.emby_api_key:
        raise HTTPException(status_code=400, detail="Emby URL or API key not configured")
    url = f"{config.emby_url.rstrip('/')}/emby/Items/{item_id}/Images/Primary"
    headers = {"X-Emby-Token": config.emby_api_key}
    try:
        async with create_client_session() as session:
            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status != 200:
                    raise HTTPException(status_code=resp.status, detail="Failed to fetch image from Emby")
                content = await resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                return Response(content=content, media_type=content_type)
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=502, detail=f"Emby connection error: {e}")


@api_router.get("/emby/classifications", tags=["Emby Helper"])
async def get_emby_classifications():
    def format_items(items_list: List) -> List:
        return [{"name": item.get("Name", 'N/A'), "id": item.get("Id", 'N/A')} for item in items_list]

    def format_rating_items(items_list: List) -> List:
        """按 Emby 官方 /OfficialRatings（Items: OfficialRatingItem[]）格式转换。"""
        if not isinstance(items_list, list):
            return []
        out = []
        seen = set()
        for item in items_list:
            if not isinstance(item, dict):
                continue
            s = str(item.get("Name") or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append({"name": s, "id": s})
        return out

    scoped, _sid = _load_server_scoped_config()
    try:
        tasks = {
            "collections": fetch_from_emby("/Items", params={"IncludeItemTypes": "BoxSet", "Recursive": "true"}, config=scoped),
            "genres": fetch_from_emby("/Genres", config=scoped),
            "tags": fetch_from_emby("/Tags", config=scoped),
            "studios": fetch_from_emby("/Studios", config=scoped),
            "official_ratings": fetch_from_emby("/OfficialRatings", config=scoped),
        }
        results_list = await asyncio.gather(*tasks.values())
        results_dict = dict(zip(tasks.keys(), results_list))
        results_dict["persons"] = []

        return {
            "collections": format_items(results_dict["collections"]),
            "genres": format_items(results_dict["genres"]),
            "tags": format_items(results_dict["tags"]),
            "studios": format_items(results_dict["studios"]),
            "persons": [],
            "official_ratings": format_rating_items(results_dict.get("official_ratings") or []),
        }
    except HTTPException as e:
        raise e

@api_router.get("/emby/persons/search", tags=["Emby Helper"])
async def search_emby_persons(query: str = Query(None), page: int = Query(1)):
    PAGE_SIZE = 100
    start_index = (page - 1) * PAGE_SIZE
    scoped, _sid = _load_server_scoped_config()
    try:
        if query:
            params = { "SearchTerm": query, "IncludeItemTypes": "Person", "Recursive": "true", "StartIndex": start_index, "Limit": PAGE_SIZE }
            persons = await fetch_from_emby("/Items", params=params, config=scoped)
        else:
            params = { "StartIndex": start_index, "Limit": PAGE_SIZE }
            persons = await fetch_from_emby("/Persons", params=params, config=scoped)
        return [{"name": item.get("Name", 'N/A'), "id": item.get("Id", 'N/A')} for item in persons]
    except HTTPException as e:
        raise e

@api_router.get("/emby/resolve-item/{item_id}", tags=["Emby Helper"])
async def resolve_emby_item(item_id: str):
    scoped, _sid = _load_server_scoped_config()
    try:
        users = await fetch_from_emby("/Users", config=scoped)
        if not users:
            raise HTTPException(status_code=500, detail="无法获取任何Emby用户用于查询")
        
        ref_user_id = users[0]['Id']
        item_details = await fetch_from_emby(f"/Users/{ref_user_id}/Items/{item_id}", config=scoped)
        
        if not item_details:
             raise HTTPException(status_code=404, detail="在Emby中未找到指定的项目ID")
        
        return {"name": item_details.get("Name", "N/A"), "id": item_details.get("Id")}

    except HTTPException as e:
        raise e

admin_app.include_router(public_router)
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


def update_virtual_library_refresh_jobs(config: AppConfig):
    """
    所有未隐藏虚拟库共用的定时入口：间隔为单库 cache_refresh_interval，否则全局；≤0 不注册。
    触发时按 resource_type 分支：rsshub → 管理端 RSS 拉取；其余 → 通知 proxy 重填磁盘缓存。
    """
    _legacy_prefixes = ("rss_refresh:", "random_refresh:", "disk_refresh:")
    for job in list(scheduler.get_jobs()):
        if job.id == "rss_refresh_job" or job.id.startswith("vlib_scheduled_refresh:"):
            scheduler.remove_job(job.id)
        elif any(job.id.startswith(p) for p in _legacy_prefixes):
            scheduler.remove_job(job.id)

    raw = config_manager.load_config(apply_active_profile=False)
    for s in raw.servers:
        if not s.enabled:
            continue
        scoped = raw.model_copy(deep=False)
        scoped.sync_active_profile_to_legacy(s)
        scoped.emby_url = s.emby_url
        scoped.emby_api_key = s.emby_api_key
        sid = str(s.id)

        def _effective_hours(vlib: VirtualLibrary) -> int:
            if vlib.cache_refresh_interval is not None:
                return int(vlib.cache_refresh_interval)
            return int(scoped.cache_refresh_interval or 0)

        for vlib in scoped.virtual_libraries:
            if vlib.hidden:
                continue
            hours = _effective_hours(vlib)
            if hours <= 0:
                continue
            job_id = f"vlib_scheduled_refresh:{sid}:{vlib.id}"
            scheduler.add_job(
                scheduled_refresh_virtual_library,
                "interval",
                hours=hours,
                id=job_id,
                replace_existing=True,
                args=[sid, vlib.id],
            )
            logger.info(
                f"Scheduled virtual library refresh: server={sid} '{vlib.name}' ({vlib.id}) "
                f"type={vlib.resource_type} every {hours} hours"
            )


def update_real_library_cover_cron(config: AppConfig):
    """管理真实库封面刷新的 cron 定时任务。"""
    # Remove legacy/non-namespaced jobs first
    for jid in ("real_lib_cover_cron",):
        try:
            scheduler.remove_job(jid)
        except Exception:
            pass

    # Remove all namespaced cover cron jobs, then rebuild
    for job in list(scheduler.get_jobs()):
        if str(job.id).startswith("real_lib_cover_cron:"):
            try:
                scheduler.remove_job(job.id)
            except Exception:
                pass

    raw = config_manager.load_config(apply_active_profile=False)
    for s in raw.servers:
        if not s.enabled:
            continue
        scoped = raw.model_copy(deep=False)
        scoped.sync_active_profile_to_legacy(s)
        sid = str(s.id)
        cron_expr = (scoped.real_library_cover_cron or "").strip()
        if not cron_expr:
            logger.info("Real library cover cron: disabled for server=%s (no cron expression).", sid)
            continue
        job_id = f"real_lib_cover_cron:{sid}"
        try:
            trigger = CronTrigger.from_crontab(cron_expr)
            scheduler.add_job(
                refresh_all_real_library_covers,
                trigger,
                id=job_id,
                replace_existing=True,
                args=[sid],
            )
            logger.info("Real library cover cron scheduled: server=%s expr='%s'", sid, cron_expr)
        except Exception as e:
            logger.error("Invalid cron expression '%s' for server=%s: %s", cron_expr, sid, e)


async def scheduled_refresh_virtual_library(server_id: str, library_id: str):
    """定时任务唯一入口：按虚拟库类型调用对应刷新实现。"""
    config, sid = _load_server_scoped_config(server_id)
    vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)
    if not vlib or vlib.hidden:
        return
    logger.info(
        f"SCHEDULED_VLIB_REFRESH START server={sid} vlib={vlib.id} name='{vlib.name}' type={vlib.resource_type}"
    )
    if vlib.resource_type == "rsshub":
        # RSS 库仍走 RSS 队列刷新；随后重生成封面
        await enqueue_rss_refresh(vlib, manual=False, wait_for_completion=True)
        await _regenerate_cover_for_vlib(vlib, server_id=sid)
    else:
        # 非 RSS：与手动「刷新数据」保持完全一致的语义（刷新磁盘缓存 + 刷新封面）
        await refresh_virtual_library_data_and_cover(vlib, server_id=sid)
    logger.info(
        f"SCHEDULED_VLIB_REFRESH DONE server={sid} vlib={vlib.id} name='{vlib.name}' type={vlib.resource_type}"
    )

async def refresh_all_rss_libraries():
    """定时刷新所有 RSS 虚拟库"""
    logger.info("Starting scheduled RSS library refresh...")
    config = config_manager.load_config()
    rss_libraries = [lib for lib in config.virtual_libraries if lib.resource_type == "rsshub" and not lib.hidden]
    
    for vlib in rss_libraries:
        logger.info(f"Queue RSS refresh: {vlib.name} ({vlib.id})")
        await enqueue_rss_refresh(vlib, manual=False, wait_for_completion=False)
    await _rss_refresh_queue.join()
    logger.info("Scheduled RSS library refresh finished.")

@admin_app.on_event("startup")
async def startup_event():
    # 启动调度器
    scheduler.start()
    # 初始化定时任务
    config = config_manager.load_config()
    update_virtual_library_refresh_jobs(config)
    update_real_library_cover_cron(config)

@admin_app.on_event("shutdown")
async def shutdown_event():
    # 关闭调度器
    scheduler.shutdown()

# SPA 静态文件服务：先挂载静态资源（js/css/图片等），再用 catch-all 路由处理前端路由
from fastapi.responses import FileResponse

admin_app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="static-assets")

# favicon 等根目录静态文件
@admin_app.get("/favicon.png")
async def favicon():
    return FileResponse(str(static_dir / "favicon.png"))

@admin_app.get("/logo.png")
async def logo():
    return FileResponse(str(static_dir / "logo.png"))

# Catch-all：所有非 API、非静态资源的请求都返回 index.html，交给前端路由处理
@admin_app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    # 如果请求的是一个实际存在的静态文件，直接返回
    file_path = static_dir / full_path
    if file_path.is_file():
        return FileResponse(str(file_path))
    # 否则返回 index.html，让前端路由接管
    return FileResponse(str(static_dir / "index.html"))
