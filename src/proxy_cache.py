# src/proxy_cache.py

from cachetools import TTLCache, Cache

# API response cache: 500 items, 5 min TTL
api_cache = TTLCache(maxsize=500, ttl=300)

# Virtual library items cache for cover generation (no TTL)
vlib_items_cache = Cache(maxsize=100)

# Random recommendation cache: per user+vlib, 12 hour TTL
random_recommend_cache = TTLCache(maxsize=200, ttl=43200)