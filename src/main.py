# src/main.py
"""
Entry points for Admin (8001) and Proxy (8999).

Use ``both`` in production (Docker) so one OS process serves both ports: a single CPython
interpreter and one copy of imported modules — the largest real RSS win vs two processes.
"""

import asyncio
import os
import sys

import uvicorn


def start_admin():
    """启动管理服务器（独立进程；本地调试用）"""
    print("--- Starting Admin Server ---")
    print("Access the Web UI at http://127.0.0.1:8011 (or your host IP)")
    print("API documentation at http://127.0.0.1:8011/docs")
    uvicorn.run("admin_server:admin_app", host="0.0.0.0", port=8001, reload=False)


def start_proxy():
    """启动代理服务器（独立进程；本地调试用）"""
    print("--- Starting Proxy Server ---")
    print("Proxy is listening on http://0.0.0.0:8999")
    uvicorn.run("proxy_server:proxy_app", host="0.0.0.0", port=8999, reload=False)


def start_both():
    """
    单进程同时监听 8001（admin）与 8999（proxy）。

    避免两套解释器 + 两套重复 import（fastapi / aiohttp / config / proxy_cache 等），
    这是把「打满事件」后总常驻压下来的主要架构手段。
    """
    from admin_server import admin_app
    from proxy_server import proxy_app

    log_level = os.environ.get("LOG_LEVEL", "info").lower()

    async def _serve() -> None:
        cfg_admin = uvicorn.Config(
            admin_app,
            host="0.0.0.0",
            port=8001,
            log_level=log_level,
        )
        cfg_proxy = uvicorn.Config(
            proxy_app,
            host="0.0.0.0",
            port=8999,
            log_level=log_level,
        )
        await asyncio.gather(
            uvicorn.Server(cfg_admin).serve(),
            uvicorn.Server(cfg_proxy).serve(),
        )

    print("--- Single process: Admin :8001 + Proxy :8999 ---")
    asyncio.run(_serve())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Please specify: admin | proxy | both")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "admin":
        start_admin()
    elif cmd == "proxy":
        start_proxy()
    elif cmd == "both":
        start_both()
    else:
        print(f"Unknown command: {cmd}")
        print("Available: admin, proxy, both")
        sys.exit(1)
