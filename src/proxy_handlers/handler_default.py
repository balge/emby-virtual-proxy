# src/proxy_handlers/handler_default.py
import logging
from fastapi import Request
from fastapi.responses import StreamingResponse, Response
from aiohttp import ClientSession, ClientError

logger = logging.getLogger(__name__)

# Headers that should not be forwarded between proxy hops
HOP_BY_HOP = frozenset({
    'transfer-encoding', 'connection', 'keep-alive',
    'proxy-authenticate', 'proxy-authorization', 'te',
    'trailers', 'upgrade',
})

async def forward_request(
    request: Request,
    full_path: str,
    method: str,
    real_emby_url: str,
    session: ClientSession,
) -> Response:
    target_url = f"{real_emby_url}/{full_path}"

    # Build request headers: remove host and hop-by-hop
    req_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() != 'host'
    }

    # Only forward body for methods that have one
    body = None
    if method in ("POST", "PUT", "PATCH"):
        body = await request.body()

    try:
        resp = await session.request(
            method=method,
            url=target_url,
            params=request.query_params,
            headers=req_headers,
            data=body,
            allow_redirects=False,
        )
    except ClientError as e:
        logger.error(f"Proxy connection error to {target_url}: {e}")
        return Response(content=f"Proxy upstream error: {e}", status_code=502)

    try:
        # Redirect: return immediately with Location header preserved
        if resp.status in (301, 302, 307, 308):
            redirect_body = await resp.read()
            redirect_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in HOP_BY_HOP
            }
            resp.release()
            return Response(content=redirect_body, status_code=resp.status, headers=redirect_headers)

        # Build response headers, preserving content-length for Range/streaming support
        resp_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in HOP_BY_HOP and k.lower() != 'content-encoding'
        }

        # For small non-streaming responses (< 1MB with known length), read fully
        content_length = resp.headers.get('Content-Length')
        content_type = resp.headers.get('Content-Type', '')
        if content_length and int(content_length) < 1_048_576 and 'video' not in content_type and 'audio' not in content_type:
            full_body = await resp.read()
            resp.release()
            return Response(content=full_body, status_code=resp.status, headers=resp_headers)

        # Stream large responses (video, audio, large files)
        async def stream_generator():
            try:
                async for chunk in resp.content.iter_chunked(65536):
                    yield chunk
            except ClientError as e:
                logger.error(f"Stream error for {full_path}: {e}")
            except Exception as e:
                logger.error(f"Unexpected stream error for {full_path}: {e}")
            finally:
                resp.release()

        return StreamingResponse(
            content=stream_generator(),
            status_code=resp.status,
            headers=resp_headers,
            media_type=content_type or None,
        )

    except Exception as e:
        logger.error(f"Proxy response error for {target_url}: {e}", exc_info=True)
        try:
            resp.release()
        except Exception:
            pass
        return Response(content=f"Proxy error: {e}", status_code=502)
