# src/config_manager.py (最终导入修正版)

import json
from pathlib import Path
from models import AppConfig # <--- 修正这里

# 定义配置文件的路径
CONFIG_DIR = Path(__file__).parent.parent / "config"
CONFIG_FILE_PATH = CONFIG_DIR / "config.json"

def load_config() -> AppConfig:
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
            if 'webhook' not in data or not isinstance(data.get('webhook'), dict):
                data['webhook'] = {}
            return AppConfig.model_validate(data)
            
    except (json.JSONDecodeError, Exception) as e:
        print(f"Error loading or parsing config file: {e}. Returning a temporary default config.")
        return AppConfig()

def save_config(config: AppConfig):
    """
    将配置对象安全地保存到文件。
    """
    try:
        CONFIG_DIR.mkdir(exist_ok=True)

        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(config.model_dump_json(by_alias=True, indent=4))
        print(f"Configuration successfully saved to {CONFIG_FILE_PATH}")
    except Exception as e:
        print(f"Error saving config file: {e}")
