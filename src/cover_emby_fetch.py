"""
Emby 封面素材下载：供虚拟库 / 真实库拉取及后台自动生成共用。

style_shelf_1（样式四）：
- 九条媒体各写 **`n.jpg`=Primary**、**`fanart_n.jpg`=宽屏背景图**：**仅 Fanart**；剧集常无 Fanart 时 **仅此一步** 允许 **Backdrop**（仍不使用 Art/Primary）。生成器只用 `fanart_*` 评选全屏底图，底栏仍只用 Primary。
其他样式：1～N 均为各条目的 Primary（与历史逻辑一致）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

async def fetch_emby_item_image_bytes(
    session: aiohttp.ClientSession,
    emby_url: str,
    api_key: str,
    item_id: str,
    image_type: str,
) -> Optional[bytes]:
    if not emby_url or not api_key or not item_id:
        return None
    base = emby_url.rstrip("/")
    url = f"{base}/emby/Items/{item_id}/Images/{image_type}"
    headers = {"X-Emby-Token": api_key}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception as e:
        logger.debug("Emby image fetch failed %s %s: %s", item_id, image_type, e)
    return None


async def save_first_matching_image(
    session: aiohttp.ClientSession,
    emby_url: str,
    api_key: str,
    item_id: str,
    types: tuple[str, ...],
    dest: Path,
) -> bool:
    for typ in types:
        data = await fetch_emby_item_image_bytes(session, emby_url, api_key, item_id, typ)
        if data:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            return True
    return False


async def download_cover_images_emby(
    session: aiohttp.ClientSession,
    emby_url: str,
    api_key: str,
    selected_items: list[dict[str, Any]],
    temp_dir: Path,
    *,
    style_shelf_1: bool = False,
) -> bool:
    """
    将图片写入 temp_dir 的 1.jpg、2.jpg…
    非 shelf：最多 len(selected_items) 张，均为 Primary。
    shelf：各槽 Primary 写入 `n.jpg`；`fanart_n.jpg` 为 **Fanart → Backdrop**（无 Art/Primary）；须至少成功 **一张宽屏背景** 与 **一张 Primary**。
    """
    if not selected_items:
        return False
    emby_url = (emby_url or "").strip()
    if style_shelf_1:
        n = len(selected_items)
        fanart_any = False
        primary_any = False
        for i in range(min(9, n)):
            iid = str(selected_items[i].get("Id") or "").strip()
            if not iid:
                continue
            fa_ok = await save_first_matching_image(
                session,
                emby_url,
                api_key,
                iid,
                ("Fanart", "Backdrop"),
                temp_dir / f"fanart_{i + 1}.jpg",
            )
            fanart_any = fanart_any or fa_ok
            ok = await save_first_matching_image(
                session,
                emby_url,
                api_key,
                iid,
                ("Primary",),
                temp_dir / f"{i + 1}.jpg",
            )
            primary_any = primary_any or ok
        return bool(fanart_any and primary_any)

    ok_any = False
    for i, item in enumerate(selected_items):
        iid = str(item.get("Id") or "").strip()
        if not iid:
            continue
        ok = await save_first_matching_image(
            session,
            emby_url,
            api_key,
            iid,
            ("Primary",),
            temp_dir / f"{i + 1}.jpg",
        )
        ok_any = ok_any or ok
    return ok_any
