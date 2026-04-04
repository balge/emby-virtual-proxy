# src/proxy_handlers/_find_helper.py (终极加固版)

import json
import logging
from typing import List, Dict
from aiohttp import ClientSession
import config_manager

logger = logging.getLogger(__name__)

async def is_item_in_a_merge_enabled_vlib(
    session: ClientSession, real_emby_url: str, user_id: str, item_id: str, headers: Dict, auth_token_param: Dict
) -> bool:
    """
    检查给定的 item_id 是否属于任何一个启用了 TMDB 合并功能的虚拟库。
    这是决定是否合并其子项目（季/集）的关键。
    此版本为终极加固版，强制进行字符串比较，以避免任何类型不匹配问题，并依赖DEBUG日志进行诊断。
    """
    config = config_manager.load_config()

    # 检查全局强制合并开关
    if config.force_merge_by_tmdb_id:
        logger.info(f"MERGE_CHECK: ✅ 全局开关已启用。允许对项目 {item_id} 进行合并。")
        return True

    merge_vlibs = [vlib for vlib in config.virtual_libraries if vlib.merge_by_tmdb_id]

    if not merge_vlibs:
        logger.debug(f"MERGE_CHECK: 没有任何虚拟库启用合并功能。跳过对项目 {item_id} 的合并检查。")
        return False

    item_details_url = f"{real_emby_url}/emby/Users/{user_id}/Items/{item_id}"
    item_params = {
        'Fields': 'CollectionIds,TagItems,GenreItems,Studios,People,ProviderIds',
        **auth_token_param
    }
    
    item = None
    try:
        async with session.get(item_details_url, params=item_params, headers=headers) as resp:
            if resp.status != 200:
                logger.warning(f"MERGE_CHECK: 无法获取项目 {item_id} 的详情。状态码: {resp.status}, 响应: {await resp.text()}")
                return False
            
            item = await resp.json()
            # 【【【 这是本次最关键的日志，请务必在 DEBUG 模式下查看 】】】
            logger.debug(f"MERGE_CHECK: 已获取项目 {item_id} ('{item.get('Name')}') 的详情用于匹配。收到的数据: \n{json.dumps(item, indent=2, ensure_ascii=False)}")

    except Exception as e:
        logger.error(f"MERGE_CHECK: 获取项目 {item_id} 详情时发生严重错误: {e}")
        return False
    
    if not item:
        return False

    for vlib in merge_vlibs:
        resource_type = vlib.resource_type
        target_ids = {str(x) for x in vlib.resolved_resource_ids()}
        match_found = False

        try:
            if resource_type == "collection":
                item_collection_ids = {str(col_id) for col_id in item.get("CollectionIds", [])}
                if target_ids & item_collection_ids:
                    match_found = True

            elif resource_type == "tag":
                item_tag_ids = {str(tag.get("Id")) for tag in item.get("TagItems", []) if tag.get("Id")}
                if target_ids & item_tag_ids:
                    match_found = True

            elif resource_type == "genre":
                item_genre_ids = {str(genre.get("Id")) for genre in item.get("GenreItems", []) if genre.get("Id")}
                if target_ids & item_genre_ids:
                    match_found = True

            elif resource_type == "studio":
                item_studio_ids = {str(studio.get("Id")) for studio in item.get("Studios", []) if studio.get("Id")}
                if target_ids & item_studio_ids:
                    match_found = True

            elif resource_type == "person":
                item_person_ids = {str(person.get("Id")) for person in item.get("People", []) if person.get("Id")}
                if target_ids & item_person_ids:
                    match_found = True

        except Exception as e:
            logger.error(f"MERGE_CHECK: 在检查虚拟库 '{vlib.name}' 时发生内部错误: {e}")
            continue
        
        if match_found:
            logger.info(f"MERGE_CHECK: ✅ 成功! 项目 {item_id} ('{item.get('Name')}') 确认位于已启用合并的虚拟库 '{vlib.name}' (类型: {resource_type}) 中。允许合并。")
            return True

    logger.info(f"MERGE_CHECK: ❌ 拒绝。项目 {item_id} ('{item.get('Name')}') 未在 {len(merge_vlibs)} 个已启用合并的虚拟库中找到。")
    return False


async def find_all_series_by_tmdb_id(
    session: ClientSession, real_emby_url: str, user_id: str, tmdb_id: str, headers: Dict, auth_token_param: Dict
) -> List[str]:
    search_url = f"{real_emby_url}/emby/Items"
    search_params = {
        'Recursive': 'true',
        'IncludeItemTypes': 'Series',
        'Fields': 'ProviderIds',
        'HasTmdbId': 'true',
        'UserId': user_id,
        **auth_token_param
    }
    logger.debug(f"正在执行全局剧集遍历搜索 (TMDB ID: {tmdb_id})")
    try:
        async with session.get(search_url, params=search_params, headers=headers, timeout=120) as resp:
            if resp.status == 200:
                data = await resp.json()
                all_series = data.get("Items", [])
                found_ids = [
                    item.get("Id") for item in all_series 
                    if str(item.get("ProviderIds", {}).get("Tmdb")) == str(tmdb_id)
                ]
                return list(set(found_ids))
            else:
                logger.error(f"全局遍历搜索失败，状态码: {resp.status}，响应: {await resp.text()}")
                return []
    except Exception as e:
        logger.error(f"全局遍历搜索时发生异常: {e}")
        return []
