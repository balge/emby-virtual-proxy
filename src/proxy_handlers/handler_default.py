# src/proxy_handlers/handler_default.py
import logging
from fastapi import Request
from fastapi.responses import StreamingResponse, Response
from aiohttp import ClientSession, ClientError

logger = logging.getLogger(__name__)

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

    req_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() != 'host'
    }

    body = None
    if method in ("POST", "PUT", "PATCH"):
        body = await request.body()
        logger.debug(f"DEFAULT_PROXY: {method} {full_path} with body size={len(body)}")
    else:
        logger.debug(f"DEFAULT_PROXY: {method} {full_path}")

    try:
        resp = await session.request(
            method=method,
            url=target_url,
            params=request.query_params,
            headers=req_headers,
            data=body,
            allow_redirects=False,
            auto_decompress=False,
        )
    except ClientError as e:
        logger.error(f"DEFAULT_PROXY: Connection error to {target_url}: {e}")
        return Response(content=f"Proxy upstream error: {e}", status_code=502)
    except Exception as e:
        logger.error(f"DEFAULT_PROXY: Unexpected connection error to {target_url}: {e}", exc_info=True)
        return Response(content=f"Proxy upstream error: {e}", status_code=502)

    upstream_status = resp.status
    upstream_ct = resp.headers.get('Content-Type', 'N/A')
    upstream_cl = resp.headers.get('Content-Length', 'N/A')
    upstream_ce = resp.headers.get('Content-Encoding', 'none')
    upstream_loc = resp.headers.get('Location', '')

    logger.info(
        f"DEFAULT_PROXY: Upstream response for {method} {full_path}: "
        f"status={upstream_status}, content-type={upstream_ct}, "
        f"content-length={upstream_cl}, content-encoding={upstream_ce}"
        f"{f', location={upstream_loc}' if upstream_loc else ''}"
    )

    try:
        # Redirect: return immediately with Location header preserved
        if resp.status in (301, 302, 307, 308):
            redirect_body = await resp.read()
            redirect_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in HOP_BY_HOP
            }
            resp.release()
            logger.info(
                f"DEFAULT_PROXY: Returning redirect {resp.status} for {full_path}, "
                f"Location={upstream_loc}, body_size={len(redirect_body)}"
            )
            return Response(content=redirect_body, status_code=resp.status, headers=redirect_headers)

        # Build response headers
        resp_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in HOP_BY_HOP
        }

        # Small non-streaming responses
        content_length = resp.headers.get('Content-Length')
        content_type = resp.headers.get('Content-Type', '')

        is_media = 'video' in content_type or 'audio' in content_type
        is_small = content_length and int(content_length) < 1_048_576

        if is_small and not is_media:
            full_body = await resp.read()
            resp.release()
            logger.debug(
                f"DEFAULT_PROXY: Small response for {full_path}: "
                f"declared_cl={content_length}, actual_body={len(full_body)}"
            )
            return Response(content=full_body, status_code=resp.status, headers=resp_headers)

        # Stream large / media responses
        logger.info(
            f"DEFAULT_PROXY: Streaming response for {full_path}: "
            f"content-type={content_type}, content-length={upstream_cl}"
        )

        async def stream_generator():
            bytes_sent = 0
            try:
                async for chunk in resp.content.iter_chunked(65536):
                    bytes_sent += len(chunk)
                    yield chunk
            except ClientError as e:
                logger.error(f"DEFAULT_PROXY: Stream ClientError for {full_path} after {bytes_sent} bytes: {e}")
            except Exception as e:
                logger.error(f"DEFAULT_PROXY: Stream error for {full_path} after {bytes_sent} bytes: {e}", exc_info=True)
            finally:
                resp.release()
                logger.debug(f"DEFAULT_PROXY: Stream finished for {full_path}, total {bytes_sent} bytes sent.")

        return StreamingResponse(
            content=stream_generator(),
            status_code=resp.status,
            headers=resp_headers,
            media_type=content_type or None,
        )

    except Exception as e:
        logger.error(f"DEFAULT_PROXY: Response processing error for {target_url}: {e}", exc_info=True)
        try:
            resp.release()
        except Exception:
            pass
        return Response(content=f"Proxy error: {e}", status_code=502)
