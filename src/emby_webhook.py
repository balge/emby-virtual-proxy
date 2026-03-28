# src/emby_webhook.py — Emby/Jellyfin 风格 Webhook 解析与防抖（不依赖 admin_server，避免循环导入）

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class WebhookDebouncer:
    """同 key 在 delay 内重复触发时取消上一次，只执行最后一次（按剧集/专辑分组）。"""

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def schedule(self, key: str, delay: float, coro: Callable[[], Awaitable[None]]) -> None:
        async with self._lock:
            t = self._tasks.get(key)
            if t and not t.done():
                t.cancel()

            async def runner():
                try:
                    await asyncio.sleep(delay)
                    await coro()
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.error(f"Webhook debounced job failed: {e}", exc_info=True)

            self._tasks[key] = asyncio.create_task(runner())


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


def classify_event(event_raw: str) -> Optional[str]:
    """返回 'add' | 'remove' | None"""
    e = (event_raw or "").lower().replace(" ", "").replace("_", "")
    if not e:
        return None
    # 删除 / 移除
    if any(x in e for x in ("itemremoved", "itemdeleted", "librarydeleted", "mediadeleted", "libraryremove")):
        return "remove"
    # 新增（含部分 Emby/Jellyfin 命名）
    if any(
        x in e
        for x in (
            "itemadded",
            "libraryadded",
            "librarynew",
            "mediafileadded",
            "pendingitem",
            "episodeadded",
            "movieadded",
            "seasonadded",
        )
    ):
        return "add"
    return None


def extract_item_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    item = payload.get("Item") or payload.get("item")
    if isinstance(item, dict):
        return item
    out: Dict[str, Any] = {}
    for k in ("Id", "ItemId", "SeriesId", "AlbumId", "Type", "ParentId", "Name", "ServerId"):
        if k in payload:
            out[k] = payload[k]
    return out


def extract_item_id(payload: Dict[str, Any], item: Dict[str, Any]) -> Optional[str]:
    for obj in (item, payload):
        if not isinstance(obj, dict):
            continue
        for k in ("Id", "ItemId", "item_id", "ItemGuid"):
            v = obj.get(k)
            if isinstance(v, str) and v:
                return v
    return None


def group_suffix_from_item(
    item: Dict[str, Any],
    payload: Dict[str, Any],
    group_by_series: bool,
    group_by_album: bool,
) -> str:
    if group_by_series:
        sid = item.get("SeriesId") or payload.get("SeriesId")
        if sid:
            return f"series:{sid}"
    if group_by_album:
        itype = (item.get("Type") or payload.get("ItemType") or payload.get("Type") or "")
        itype_l = itype.lower() if isinstance(itype, str) else ""
        if itype_l == "musicalbum":
            iid = item.get("Id")
            if iid:
                return f"album:{iid}"
        if itype_l in ("audio", "musicartist"):
            aid = item.get("AlbumId") or item.get("ParentId")
            if aid:
                return f"album:{aid}"
    iid = extract_item_id(payload, item)
    return f"item:{iid or 'na'}"


def extract_library_hints(payload: Dict[str, Any], item: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """返回 (library_id, library_name) 其一或二者"""
    for obj in (item, payload):
        if not isinstance(obj, dict):
            continue
        lid = obj.get("LibraryId") or obj.get("libraryId") or obj.get("MediaSourceId")
        if lid and isinstance(lid, str):
            return lid, None
    for obj in (payload, item):
        if not isinstance(obj, dict):
            continue
        for k in ("LibraryName", "MediaLibraryName", "CollectionName"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return None, v.strip()
    return None, None
