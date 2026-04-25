#!/usr/bin/env python3
"""
从 Emby 指定媒体库（默认「国产剧」）按入库时间降序取图，生成 4 种 APNG 动图演示。
输出:
  config/images/demo_cn_drama_4apng/
    - style_multi_1_animated_apng.png
    - style_single_1_animated_apng.png
    - style_single_2_animated_apng.png
    - style_shelf_1_animated_apng.png

用法:
  EMBY_API_KEY=xxx PYTHONPATH=src python3 scripts/gen_demo_cn_drama_4_apng.py
"""
from __future__ import annotations

import asyncio
import base64
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

EMBY_URL = (os.environ.get("EMBY_URL") or "http://192.168.9.110:8096").rstrip("/")
EMBY_KEY = (os.environ.get("EMBY_API_KEY") or "").strip()
LIB_NAME = os.environ.get("LIB_NAME") or "国产剧"
OUT_BASE = Path(
    os.environ.get("OUT_DIR")
    or (ROOT / "config" / "images" / "demo_cn_drama_4apng")
)

ANIM_DURATION = int(os.environ.get("DEMO_ANIM_DURATION") or "8")
ANIM_FPS = int(os.environ.get("DEMO_ANIM_FPS") or "24")
ANIM_IMAGE_COUNT = int(os.environ.get("DEMO_ANIM_IMAGE_COUNT") or "6")
DEMO_STYLE = (os.environ.get("DEMO_STYLE") or "all").strip().lower()


def _pick_fonts() -> tuple[str, str]:
    zh_candidates = [
        ROOT / "src" / "assets" / "fonts" / "multi_1_zh.ttf",
        Path("/app/src/assets/fonts/multi_1_zh.ttf"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
    ]
    en_candidates = [
        ROOT / "src" / "assets" / "fonts" / "multi_1_en.ttf",
        Path("/app/src/assets/fonts/multi_1_en.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    zh = next((str(p) for p in zh_candidates if p.is_file()), None)
    en = next((str(p) for p in en_candidates if p.is_file()), None)
    if not zh or not en:
        raise SystemExit("未找到默认字体，请检查 src/assets/fonts 或系统字体。")
    return zh, en


async def _emby_json(session, path: str, params: dict | None = None):
    import aiohttp

    url = f"{EMBY_URL}/emby{path}"
    headers = {"X-Emby-Token": EMBY_KEY, "Accept": "application/json"}
    async with session.get(
        url,
        headers=headers,
        params=params or {},
        timeout=aiohttp.ClientTimeout(total=60),
    ) as r:
        r.raise_for_status()
        return await r.json()


async def _find_library_and_user(session) -> tuple[str, str]:
    users = await _emby_json(session, "/Users")
    if not isinstance(users, list) or not users:
        raise RuntimeError("Emby /Users 返回为空")
    users_sorted = sorted(
        users,
        key=lambda u: (
            0 if (u.get("Policy") or {}).get("IsAdministrator") else 1,
            str(u.get("Name") or "").lower(),
        ),
    )
    user_id = str(users_sorted[0].get("Id") or "").strip()
    if not user_id:
        raise RuntimeError("无法获取有效用户 ID")

    libs: dict[str, dict] = {}
    try:
        mf = await _emby_json(session, "/Library/MediaFolders")
        items = mf if isinstance(mf, list) else mf.get("Items", [])
        for lib in items:
            lid = str(lib.get("Id") or "").strip()
            if lid:
                libs[lid] = lib
    except Exception:
        pass
    views = await _emby_json(session, f"/Users/{user_id}/Views")
    vitems = views if isinstance(views, list) else views.get("Items", [])
    for lib in vitems:
        lid = str(lib.get("Id") or "").strip()
        if lid and lid not in libs:
            libs[lid] = lib

    for lid, lib in libs.items():
        if str(lib.get("Name") or "") in (LIB_NAME,):
            return lid, user_id
    for lid, lib in libs.items():
        if LIB_NAME in str(lib.get("Name") or ""):
            return lid, user_id
    raise RuntimeError(f"未找到媒体库：{LIB_NAME}")


def _has_image(item: dict) -> bool:
    tags = item.get("ImageTags") or {}
    return bool(tags.get("Primary") or tags.get("Thumb") or tags.get("Screenshot"))


async def _fetch_latest_items(session, user_id: str, library_id: str, limit: int = 48) -> list[dict]:
    data = await _emby_json(
        session,
        f"/Users/{user_id}/Items",
        {
            "ParentId": library_id,
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Series,Video",
            "Fields": "ImageTags,DateCreated",
            "SortBy": "DateCreated",
            "SortOrder": "Descending",
            "Limit": str(limit),
        },
    )
    items = data.get("Items", []) if isinstance(data, dict) else data
    return list(items) if isinstance(items, list) else []


async def _download_covers(session, items: list[dict], dest: Path, *, shelf: bool) -> None:
    from cover_emby_fetch import download_cover_images_emby

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    ok = await download_cover_images_emby(
        session,
        EMBY_URL,
        EMBY_KEY,
        items,
        dest,
        style_shelf_1=shelf,
    )
    if not ok:
        raise RuntimeError(f"下载封面素材失败: {dest}")


def _prepare_single_folder(src: Path, dest: Path, n: int) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    slots = []
    for i in range(1, 10):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = src / f"{i}{ext}"
            if p.is_file():
                slots.append(p)
                break
    if len(slots) < 2:
        raise RuntimeError("单图动画素材不足（<2）")
    for j, p in enumerate(slots[: max(6, n + 2)]):
        shutil.copy2(p, dest / f"{j+1:02d}{p.suffix}")


def _style_selected(style_id: int) -> bool:
    if DEMO_STYLE in ("", "all", "*"):
        return True
    return DEMO_STYLE == str(style_id)


async def main() -> None:
    from http_client import create_client_session
    from cover_generator.style_multi_1_animated import create_style_multi_1_animated
    from cover_generator.style_single_1_animated import create_style_single_1_animated
    from cover_generator.style_single_2_animated import create_style_single_2_animated
    from cover_generator.style_shelf_1_animated import create_style_shelf_1_animated

    if not EMBY_KEY:
        raise SystemExit("请设置 EMBY_API_KEY")

    OUT_BASE.mkdir(parents=True, exist_ok=True)
    zh_font, en_font = _pick_fonts()
    title = ("国产剧", "CHINESE DRAMA")
    font_path = (zh_font, en_font)

    async with create_client_session() as session:
        lib_id, user_id = await _find_library_and_user(session)
        items = await _fetch_latest_items(session, user_id, lib_id, limit=48)
        with_img = [x for x in items if _has_image(x)][:9]
        single_items = [x for x in items if _has_image(x)][: max(9, ANIM_IMAGE_COUNT + 3)]
        if len(with_img) < 2:
            raise RuntimeError("带图条目不足")

        work_multi = OUT_BASE / "_work_multi"
        work_shelf = OUT_BASE / "_work_shelf"
        work_single_src = OUT_BASE / "_work_single_src"
        work_single_1 = OUT_BASE / "_work_single_1"
        work_single_2 = OUT_BASE / "_work_single_2"
        await _download_covers(session, with_img, work_multi, shelf=False)
        await _download_covers(session, with_img, work_shelf, shelf=True)
        await _download_covers(session, single_items, work_single_src, shelf=False)
        _prepare_single_folder(work_single_src, work_single_1, ANIM_IMAGE_COUNT)
        _prepare_single_folder(work_single_src, work_single_2, ANIM_IMAGE_COUNT)

    jobs = [
        (
            1,
            "style_multi_1_animated_apng.png",
            lambda: create_style_multi_1_animated(
                str(work_multi),
                title,
                font_path,
                font_size=(1, 1.2),
                animation_duration=ANIM_DURATION,
                animation_fps=ANIM_FPS,
                animation_format="apng",
                scroll_direction="alternate",
            ),
        ),
        (
            2,
            "style_single_1_animated_apng.png",
            lambda: create_style_single_1_animated(
                str(work_single_1),
                title,
                font_path,
                font_size=(1, 1.2),
                animation_duration=ANIM_DURATION,
                animation_fps=ANIM_FPS,
                animation_format="apng",
                image_count=ANIM_IMAGE_COUNT,
                departure_type="fly",
            ),
        ),
        (
            3,
            "style_single_2_animated_apng.png",
            lambda: create_style_single_2_animated(
                str(work_single_2),
                title,
                font_path,
                font_size=(1, 1.2),
                animation_duration=ANIM_DURATION,
                animation_fps=ANIM_FPS,
                animation_format="apng",
                image_count=ANIM_IMAGE_COUNT,
            ),
        ),
        (
            4,
            "style_shelf_1_animated_apng.png",
            lambda: create_style_shelf_1_animated(
                str(work_shelf),
                title,
                font_path,
                font_size=(0.86, 0.94),
                animation_duration=ANIM_DURATION,
                animation_fps=ANIM_FPS,
                animation_format="apng",
                scroll_direction="alternate",
            ),
        ),
    ]

    jobs = [j for j in jobs if _style_selected(j[0])]
    if not jobs:
        raise RuntimeError(f"DEMO_STYLE={DEMO_STYLE!r} 无效，可用值：all/1/2/3/4")

    for _sid, name, fn in jobs:
        print("生成", name, "...")
        b64 = fn()
        if not b64:
            raise RuntimeError(f"生成失败: {name}")
        out = OUT_BASE / name
        out.write_bytes(base64.b64decode(b64))
        print("  ->", out, "size", out.stat().st_size)

    for d in (work_multi, work_shelf, work_single_src, work_single_1, work_single_2):
        shutil.rmtree(d, ignore_errors=True)
    print("完成。输出目录:", OUT_BASE)


if __name__ == "__main__":
    asyncio.run(main())

