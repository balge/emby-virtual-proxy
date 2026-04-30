from __future__ import annotations

from typing import Any, Dict, List

from models import RealLibraryConfig


def sync_real_library_configs(
    existing: List[RealLibraryConfig],
    emby_libs: List[Dict[str, Any]],
) -> List[RealLibraryConfig]:
    """以 Emby 当前真实库为准，保留已有开关与封面配置。"""
    existing_map = {str(rl.id): rl for rl in (existing or [])}
    synced: List[RealLibraryConfig] = []
    for lib in emby_libs:
        lib_id = str(lib.get("Id") or "").strip()
        if not lib_id:
            continue
        lib_name = str(lib.get("Name") or "").strip()
        if lib_id in existing_map:
            rl = existing_map[lib_id]
            rl.name = lib_name
            synced.append(rl)
        else:
            synced.append(RealLibraryConfig(id=lib_id, name=lib_name, enabled=True))
    return synced


def prune_source_library_ids(
    source_ids: List[str],
    *,
    valid_real_ids: set[str],
    disabled_real_ids: set[str],
) -> List[str]:
    """移除虚拟库绑定中已删除或已禁用的真实库 ID。"""
    normalized = [str(x).strip() for x in (source_ids or []) if str(x).strip()]
    return [lid for lid in normalized if lid in valid_real_ids and lid not in disabled_real_ids]
