#!/usr/bin/env python3
"""
从指定 Emby 的「国产剧」媒体库按入库时间降序拉图，生成 4 种动图演示（GIF）。
字体/字号与 cover_worker 默认一致；动图时长/帧率可用环境变量覆盖（默认与 models.AppConfig 一致：8s / 24fps）。

用法（在项目根目录）:
  EMBY_API_KEY=你的密钥 PYTHONPATH=src python3 scripts/gen_demo_cn_drama_4_animated.py

环境变量:
  EMBY_API_KEY（必填）  EMBY_URL（默认 http://192.168.9.110:8096）  LIB_NAME  OUT_DIR
  DEMO_ANIM_DURATION / DEMO_ANIM_FPS / DEMO_ANIM_IMAGE_COUNT（与 AppConfig 默认一致：8 / 24 / 6）
  DEMO_SINGLE_DURATION / DEMO_SINGLE_FPS（仅样式二/三动图；不设则与上面相同。单图链式帧多，演示可设为 4 与 10 以缩短时间与体积）
"""
from __future__ import annotations

import asyncio
import base64
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

EMBY_URL = (os.environ.get("EMBY_URL") or "http://192.168.9.110:8096").rstrip("/")
EMBY_KEY = (os.environ.get("EMBY_API_KEY") or "").strip()
LIB_NAME = os.environ.get("LIB_NAME") or "国产剧"
OUT_BASE = Path(os.environ.get("OUT_DIR") or (ROOT / "config" / "images" / "demo_cn_drama_4animated"))

# 与 models.AppConfig 默认一致；可用环境变量缩短演示耗时
ANIM_DURATION = int(os.environ.get("DEMO_ANIM_DURATION") or "12")
ANIM_FPS = int(os.environ.get("DEMO_ANIM_FPS") or "24")
ANIM_IMAGE_COUNT = int(os.environ.get("DEMO_ANIM_IMAGE_COUNT") or "6")
SINGLE_DURATION = int(os.environ.get("DEMO_SINGLE_DURATION") or os.environ.get("DEMO_ANIM_DURATION") or "12")
SINGLE_FPS = int(os.environ.get("DEMO_SINGLE_FPS") or os.environ.get("DEMO_ANIM_FPS") or "24")


def _pick_fonts() -> tuple[str, str]:
    zh_candidates = [
        ROOT / "src" / "assets" / "fonts" / "multi_1_zh.ttf",
        Path("/app/src/assets/fonts/multi_1_zh.ttf"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    ]
    en_candidates = [
        ROOT / "src" / "assets" / "fonts" / "multi_1_en.ttf",
        Path("/app/src/assets/fonts/multi_1_en.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
    ]
    zh = next((str(p) for p in zh_candidates if p.is_file()), None)
    en = next((str(p) for p in en_candidates if p.is_file()), None)
    if not zh or not en:
        raise SystemExit("未找到中/英文字体，请在 Docker 内运行或安装系统字体。")
    return zh, en


async def _emby_json(session, path: str, params: dict | None = None) -> dict | list:
    import aiohttp

    url = f"{EMBY_URL}/emby{path}"
    headers = {"X-Emby-Token": EMBY_KEY, "Accept": "application/json"}
    async with session.get(url, headers=headers, params=params or {}, timeout=aiohttp.ClientTimeout(total=60)) as r:
        r.raise_for_status()
        return await r.json()


async def _find_library_id(session) -> tuple[str, str]:
    """返回 (library_id, user_id)。"""
    users = await _emby_json(session, "/Users")
    if not isinstance(users, list) or not users:
        raise RuntimeError("Emby /Users 为空")
    users_sorted = sorted(
        users,
        key=lambda u: (
            0 if (u.get("Policy") or {}).get("IsAdministrator") else 1,
            str(u.get("Name") or "").lower(),
        ),
    )
    user_id = str(users_sorted[0].get("Id") or "").strip()
    if not user_id:
        raise RuntimeError("无效用户 Id")

    libs: dict[str, dict] = {}
    try:
        mf = await _emby_json(session, "/Library/MediaFolders")
        items = mf if isinstance(mf, list) else mf.get("Items", [])
        for lib in items:
            lid = lib.get("Id")
            if lid:
                libs[str(lid)] = lib
    except Exception:
        pass

    views = await _emby_json(session, f"/Users/{user_id}/Views")
    vitems = views if isinstance(views, list) else views.get("Items", [])
    for lib in vitems:
        lid = lib.get("Id")
        if lid and str(lid) not in libs:
            libs[str(lid)] = lib

    for lid, lib in libs.items():
        name = str(lib.get("Name") or "")
        if LIB_NAME in name or name == LIB_NAME:
            return lid, user_id
    names = [libs[k].get("Name") for k in libs]
    raise RuntimeError(f"未找到名为「{LIB_NAME}」的库。可用库: {names}")


async def _fetch_library_items(session, user_id: str, library_id: str, limit: int = 40) -> list[dict]:
    base = f"/Users/{user_id}/Items"
    params = {
        "ParentId": library_id,
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Series,Video",
        "Fields": "ImageTags,DateCreated",
        "SortBy": "DateCreated",
        "SortOrder": "Descending",
        "Limit": str(limit),
    }
    data = await _emby_json(session, base, params=params)
    items = data.get("Items", []) if isinstance(data, dict) else data
    return list(items) if isinstance(items, list) else []


def _has_image(item: dict) -> bool:
    tags = item.get("ImageTags") or {}
    return bool(tags.get("Primary") or tags.get("Thumb") or tags.get("Screenshot"))


async def _download_covers(session, items: list[dict], dest: Path, *, shelf: bool) -> None:
    from cover_emby_fetch import download_cover_images_emby

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    cfg = SimpleNamespace(emby_url=EMBY_URL, emby_api_key=EMBY_KEY)
    ok = await download_cover_images_emby(
        session,
        EMBY_URL,
        EMBY_KEY,
        items,
        dest,
        style_shelf_1=shelf,
    )
    if not ok:
        raise RuntimeError("封面素材下载失败")


def _prepare_single_style_folder(src: Path, dest: Path, n: int) -> None:
    """按 01.jpg… 命名，保证排序与入库顺序一致。"""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    # 优先数字槽位 1..9
    slots = []
    for i in range(1, 10):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = src / f"{i}{ext}"
            if p.is_file():
                slots.append(p)
                break
    if len(slots) < 2:
        raise RuntimeError("单图动效至少需要 2 张槽位图")
    for j, p in enumerate(slots[: max(n, 6)]):
        shutil.copy2(p, dest / f"{j + 1:02d}{p.suffix}")


async def main() -> None:
    import aiohttp
    from http_client import create_client_session

    if not EMBY_KEY:
        raise SystemExit("请设置环境变量 EMBY_API_KEY（Emby API 密钥）。")

    zh_font, en_font = _pick_fonts()
    title = ("国产剧", "CHINESE DRAMA")
    font_path = (zh_font, en_font)

    OUT_BASE.mkdir(parents=True, exist_ok=True)

    async with create_client_session() as session:
        lib_id, user_id = await _find_library_id(session)
        items = await _fetch_library_items(session, user_id, lib_id, limit=48)
        with_img = [x for x in items if _has_image(x)][:9]
        if len(with_img) < 2:
            raise RuntimeError("库内带图条目不足")
        more = [x for x in items if _has_image(x)][: max(9, ANIM_IMAGE_COUNT + 2)]
        # 单图动效需要多几张不同图：尽量多取
        single_items = [x for x in items if _has_image(x)][: max(9, ANIM_IMAGE_COUNT + 3)]

        work_multi = OUT_BASE / "_work_multi"
        await _download_covers(session, with_img, work_multi, shelf=False)

        work_shelf = OUT_BASE / "_work_shelf"
        await _download_covers(session, with_img, work_shelf, shelf=True)

        work_single = OUT_BASE / "_work_single_src"
        await _download_covers(session, single_items, work_single, shelf=False)
        work_single_a = OUT_BASE / "_work_single1"
        work_single_b = OUT_BASE / "_work_single2"
        _prepare_single_style_folder(work_single, work_single_a, ANIM_IMAGE_COUNT + 2)
        _prepare_single_style_folder(work_single, work_single_b, ANIM_IMAGE_COUNT + 2)

    from cover_generator.style_multi_1_animated import create_style_multi_1_animated
    from cover_generator.style_single_1_animated import create_style_single_1_animated
    from cover_generator.style_single_2_animated import create_style_single_2_animated
    from cover_generator.style_shelf_1_animated import create_style_shelf_1_animated

    anim_multi_shelf = dict(animation_duration=ANIM_DURATION, animation_fps=ANIM_FPS)
    anim_single = dict(
        animation_duration=SINGLE_DURATION,
        animation_fps=SINGLE_FPS,
        image_count=ANIM_IMAGE_COUNT,
    )

    jobs = [
        (
            "style_multi_1_animated.gif",
            lambda: create_style_multi_1_animated(
                str(work_multi),
                title,
                font_path,
                font_size=(1, 1.2),
                scroll_direction="alternate",
                **anim_multi_shelf,
            ),
        ),
        (
            "style_single_1_animated.gif",
            lambda: create_style_single_1_animated(
                str(work_single_a),
                title,
                font_path,
                font_size=(1, 1.2),
                departure_type="fly",
                **anim_single,
            ),
        ),
        (
            "style_single_2_animated.gif",
            lambda: create_style_single_2_animated(
                str(work_single_b),
                title,
                font_path,
                font_size=(1, 1.2),
                **anim_single,
            ),
        ),
        (
            "style_shelf_1_animated.gif",
            lambda: create_style_shelf_1_animated(
                str(work_shelf),
                title,
                font_path,
                font_size=(0.86, 0.94),
                scroll_direction="alternate",
                **anim_multi_shelf,
            ),
        ),
    ]

    for name, fn in jobs:
        print("生成", name, "...")
        b64 = fn()
        if not b64:
            raise RuntimeError(f"生成失败: {name}")
        out = OUT_BASE / name
        out.write_bytes(base64.b64decode(b64))
        print("  ->", out, "size", out.stat().st_size)

    for d in (OUT_BASE / "_work_multi", OUT_BASE / "_work_shelf", OUT_BASE / "_work_single_src", OUT_BASE / "_work_single1", OUT_BASE / "_work_single2"):
        shutil.rmtree(d, ignore_errors=True)
    print("完成。输出目录:", OUT_BASE)


if __name__ == "__main__":
    asyncio.run(main())
