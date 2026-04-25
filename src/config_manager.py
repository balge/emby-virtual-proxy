# src/config_manager.py (最终导入修正版)

import json
from pathlib import Path
from models import AppConfig # <--- 修正这里

# 定义配置文件的路径
CONFIG_DIR = Path(__file__).parent.parent / "config"
CONFIG_FILE_PATH = CONFIG_DIR / "config.json"

def load_config(apply_active_profile: bool = True) -> AppConfig:
    """
    加载配置文件。如果目录或文件不存在，则使用默认值自动创建。
    """
    try:
        CONFIG_DIR.mkdir(exist_ok=True)
        
        if not CONFIG_FILE_PATH.is_file():
            print("Config file not found. Creating a new one with default values.")
            default_config = AppConfig()
            save_config(default_config)
            return default_config

        with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # --- 核心修复：确保新字段存在以兼容旧配置文件 ---
            if 'advanced_filters' not in data:
                data['advanced_filters'] = []
            if 'show_missing_episodes' not in data:
                data['show_missing_episodes'] = False
            if 'tmdb_api_key' not in data:
                data['tmdb_api_key'] = ""
            if 'tmdb_proxy' not in data:
                data['tmdb_proxy'] = ""
            # Backward compatibility: migrate old rss_refresh_interval -> cache_refresh_interval
            if 'cache_refresh_interval' not in data and 'rss_refresh_interval' in data:
                data['cache_refresh_interval'] = data.get('rss_refresh_interval')
            libs = data.get('library') if isinstance(data.get('library'), list) else data.get('virtual_libraries')
            if isinstance(libs, list):
                for lib in libs:
                    if isinstance(lib, dict) and 'cache_refresh_interval' not in lib and 'rss_refresh_interval' in lib:
                        lib['cache_refresh_interval'] = lib.get('rss_refresh_interval')
            if 'webhook' not in data or not isinstance(data.get('webhook'), dict):
                data['webhook'] = {}
            cfg = AppConfig.model_validate(data)
            # Migrate legacy single-server config into servers[] in memory.
            cfg.ensure_servers_migrated()
            if apply_active_profile:
                cfg.sync_active_profile_to_legacy()
            return cfg
            
    except (json.JSONDecodeError, Exception) as e:
        print(f"Error loading or parsing config file: {e}. Returning a temporary default config.")
        cfg = AppConfig()
        cfg.ensure_servers_migrated()
        if apply_active_profile:
            cfg.sync_active_profile_to_legacy()
        return cfg

def save_config(config: AppConfig, sync_active_profile: bool = True):
    """
    将配置对象安全地保存到文件。
    """
    try:
        CONFIG_DIR.mkdir(exist_ok=True)
        # Persist current edited top-level settings into selected server profile.
        if sync_active_profile:
            config.sync_legacy_to_active_profile()

        # Write a canonical config format:
        # - servers[].profile is the source of truth for per-server settings
        # - keep advanced_filters (and other truly global settings) at top-level
        # - avoid duplicating per-server fields at top-level to reduce confusion
        data = config.model_dump(by_alias=True)
        per_server_keys = {
            "enable_cache",
            "display_order",
            "ignore_libraries",
            "real_libraries",
            "real_library_cover_cron",
            "hide",
            "library",
            "virtual_libraries",
            "default_cover_style",
            "cover_style_variant",
            "animation_duration",
            "animation_fps",
            "animated_image_count",
            "animated_departure_type",
            "animated_scroll_direction",
            "show_missing_episodes",
            "cache_refresh_interval",
            "webhook",
            "force_merge_by_tmdb_id",
        }
        for k in per_server_keys:
            data.pop(k, None)

        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=4))
        print(f"Configuration successfully saved to {CONFIG_FILE_PATH}")
    except Exception as e:
        print(f"Error saving config file: {e}")
