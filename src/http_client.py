"""
Bounded aiohttp ClientSession factory (caps total / per-host connections).

Avoids unbounded TCPConnector defaults under concurrent traffic (FD / memory).
"""

from __future__ import annotations

import os

import aiohttp


def create_client_session(**kwargs) -> aiohttp.ClientSession:
    if kwargs.get("connector") is not None:
        return aiohttp.ClientSession(**kwargs)
    cookie_jar = kwargs.pop("cookie_jar", None)
    limit = int(kwargs.pop("limit", os.environ.get("AIOHTTP_POOL_LIMIT", "32")))
    limit_per_host = int(
        kwargs.pop("limit_per_host", os.environ.get("AIOHTTP_POOL_PER_HOST", "8"))
    )
    connector = aiohttp.TCPConnector(
        limit=limit,
        limit_per_host=limit_per_host,
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
    )
    session_kw: dict = {"connector": connector, **kwargs}
    if cookie_jar is not None:
        session_kw["cookie_jar"] = cookie_jar
    return aiohttp.ClientSession(**session_kw)
