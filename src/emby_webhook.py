# src/emby_webhook.py — Emby/Jellyfin 风格 Webhook 解析（不依赖 admin_server，避免循环导入）

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

SUPPORTED_EVENTS = {"library.new", "library.deleted"}


def parse_request_payload(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if not isinstance(raw, dict):
        return {}
    merged = dict(raw)
    for k in ("Data", "data", "Body", "body", "Payload", "payload"):
        inner = raw.get(k)
        if isinstance(inner, dict):
            for ik, iv in inner.items():
                if ik not in merged or merged[ik] is None:
                    merged[ik] = iv
            break
    return merged


def extract_event_raw(payload: Dict[str, Any]) -> str:
    for k in ("NotificationType", "Notification", "Event", "event", "Type", "action"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def extract_item_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    item = payload.get("Item") or payload.get("item")
    if isinstance(item, dict):
        return item
    out: Dict[str, Any] = {}
    for k in ("Id", "ItemId", "SeriesId", "AlbumId", "Type", "ParentId", "Name", "ServerId", "Path"):
        if k in payload:
            out[k] = payload[k]
    return out
