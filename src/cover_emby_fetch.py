"""
Emby 封面素材下载：供虚拟库 / 真实库拉取及后台自动生成共用。

style_shelf_1（样式四）：
- 九条媒体各写 **`n.jpg`=主封面图（Primary→Thumb→Screenshot）**、**`fanart_n.jpg`=宽屏图（Fanart→Backdrop）**。
- 若九槽均无宽屏图，生成器 **退回用槽 1 的主封面图** 作全屏底图；下载侧只需 **至少一张主封面图** 成功即可继续。
其他样式：1～N 均为各条目的主封面图（Primary→Thumb→Screenshot）。
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
    非 shelf：最多 len(selected_items) 张，写入主封面图（Primary→Thumb→Screenshot）。
    shelf：各槽主封面图写入 `n.jpg`；`fanart_n.jpg` 为 **Fanart → Backdrop**（可全部失败）。
    须至少成功 **一张主封面图**（通常 `1.jpg`）。
    """
    if not selected_items:
        return False
    # 进入封面生成前统一按媒体 Id 去重（保序）：
    # 样式层不再承担“是否去重”的职责，确保静态/动态样式 1/2/3/4 输入一致。
    deduped_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in selected_items:
        iid = str(item.get("Id") or "").strip()
        if not iid or iid in seen_ids:
            continue
        seen_ids.add(iid)
        deduped_items.append(item)
    selected_items = deduped_items
    if not selected_items:
        return False
    emby_url = (emby_url or "").strip()
    if style_shelf_1:
        n = len(selected_items)
        primary_any = False
        for i in range(min(9, n)):
            iid = str(selected_items[i].get("Id") or "").strip()
            if not iid:
                continue
            await save_first_matching_image(
                session,
                emby_url,
                api_key,
                iid,
                ("Fanart", "Backdrop"),
                temp_dir / f"fanart_{i + 1}.jpg",
            )
            ok = await save_first_matching_image(
                session,
                emby_url,
                api_key,
                iid,
                ("Primary", "Thumb", "Screenshot"),
                temp_dir / f"{i + 1}.jpg",
            )
            primary_any = primary_any or ok
        return bool(primary_any)

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
            ("Primary", "Thumb", "Screenshot"),
            temp_dir / f"{i + 1}.jpg",
        )
        ok_any = ok_any or ok
    return ok_any
