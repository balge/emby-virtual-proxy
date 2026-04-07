# src/proxy_handlers/handler_default.py (完整流式优化版)
import logging
from fastapi import Request
from fastapi.responses import StreamingResponse, Response
from aiohttp import ClientSession, ClientError

from proxy_cache import api_cache, get_api_cache_key

logger = logging.getLogger(__name__)

# 超过此大小的 JSON 响应不进入内存缓存，避免大列表占满 api_cache
_MAX_JSON_CACHE_BYTES = 4 * 1024 * 1024


def _response_headers_from_upstream(resp) -> dict:
    return {
        k: v
        for k, v in resp.headers.items()
        if k.lower()
        not in ("transfer-encoding", "connection", "content-encoding", "content-length")
    }


async def forward_request(
    request: Request,
    full_path: str,
    method: str,
    real_emby_url: str,
    session: ClientSession,
    *,
    enable_api_cache: bool = True,
) -> Response:
    """
    默认转发：大文件仍流式；在 enable_api_cache 且为可键控的 GET 时，
    对不超过大小的 application/json 响应做 api_cache 读写。
    """
    target_url = f"{real_emby_url}/{full_path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

    cache_key: str | None = None
    if enable_api_cache and method == "GET":
        cache_key = get_api_cache_key(request, full_path)
        if cache_key and cache_key in api_cache:
            status, body, media_type = api_cache[cache_key]
            logger.debug("api_cache HIT %s", full_path[:80])
            return Response(
                content=body,
                status_code=status,
                media_type=media_type,
                headers={"Cache-Control": "private, max-age=60"},
            )

    try:
        resp = await session.request(
            method=method,
            url=target_url,
            params=request.query_params,
            headers=headers,
            data=request.stream(),
            allow_redirects=False,
        )

        if (
            enable_api_cache
            and method == "GET"
            and cache_key
            and resp.status == 200
        ):
            try:
                body = await resp.read()
                ct = resp.headers.get("Content-Type", "")
                out_headers = _response_headers_from_upstream(resp)
                if (
                    "application/json" in ct.lower()
                    and len(body) <= _MAX_JSON_CACHE_BYTES
                ):
                    api_cache[cache_key] = (resp.status, body, ct)
                    logger.debug("api_cache SET %s (%s bytes)", full_path[:80], len(body))
                return Response(
                    content=body,
                    status_code=resp.status,
                    headers=out_headers,
                    media_type=ct,
                )
            finally:
                resp.release()

        async def stream_generator():
            try:
                async for chunk in resp.content.iter_chunked(8192):
                    yield chunk
            except ClientError as e:
                logger.error(f"Error while streaming response from Emby for {full_path}: {e}")
            finally:
                resp.release()

        response_headers = _response_headers_from_upstream(resp)
        return StreamingResponse(
            content=stream_generator(),
            status_code=resp.status,
            headers=response_headers,
            media_type=resp.headers.get("Content-Type"),
        )

    except ClientError as e:
        logger.error(f"Proxy connection error to {target_url}: {e}")
        return Response(
            content=f"Error connecting to upstream Emby server: {e}",
            status_code=502,
        )
