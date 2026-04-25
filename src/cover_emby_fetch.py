"""
Emby 封面素材下载：供虚拟库 / 真实库拉取及后台自动生成共用。

style_shelf_1（样式四）：
- 1.jpg：第 **1** 条媒体的 Fanart → Art → Backdrop → Primary（底图）
- 2～9.jpg：第 **2～9** 条媒体的 Primary，**最多 8 张**预选（与其它样式一致共取 9 条）；生成器按内容去重后取底栏 5 槽，不足用底图补齐。
其他样式：1～N 均为各条目的 Primary（与历史逻辑一致）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

SHELF_BACKGROUND_IMAGE_TYPES = ("Fanart", "Art", "Backdrop", "Primary")


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
    shelf：必须成功写入 1.jpg（媒体[0] 底图）；2～9.jpg 为媒体[1]～[min(8,n-1)] 的 Primary，最多 8 张预选（可部分失败，由生成器去重后取 5 槽并用底图补齐）。
    """
    if not selected_items:
        return False
    emby_url = (emby_url or "").strip()
    if style_shelf_1:
        first_id = str(selected_items[0].get("Id") or "").strip()
        if not first_id:
            return False
        bg_ok = await save_first_matching_image(
            session,
            emby_url,
            api_key,
            first_id,
            SHELF_BACKGROUND_IMAGE_TYPES,
            temp_dir / "1.jpg",
        )
        if not bg_ok:
            return False
        n = len(selected_items)
        # 2.jpg … 9.jpg：最多 8 个条目（索引 1…8），与其它封面同样只拉 9 条；不写索引 0，避免与 1.jpg 同源。
        for j in range(8):
            item_idx = 1 + j
            if item_idx >= n:
                break
            item = selected_items[item_idx]
            iid = str(item.get("Id") or "").strip()
            if not iid:
                continue
            await save_first_matching_image(
                session,
                emby_url,
                api_key,
                iid,
                ("Primary",),
                temp_dir / f"{j + 2}.jpg",
            )
        return True

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
