# src/models.py (Final Corrected Version)

from pydantic import BaseModel, Field, ConfigDict
from typing import List, Literal, Optional
import uuid

class AdvancedFilterRule(BaseModel):
    field: str
    operator: Literal[
        "equals", "not_equals",
        "contains", "not_contains",
        "greater_than", "less_than",
        "is_empty", "is_not_empty"
    ]
    value: Optional[str] = None
    relative_days: Optional[int] = None # 新增：用于存储相对日期（例如 30 天）

class AdvancedFilter(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    match_all: bool = Field(default=True)
    rules: List[AdvancedFilterRule] = Field(default_factory=list)
    sort_field: Optional[str] = Field(default=None)  # None = emby原生排序
    sort_order: Optional[str] = Field(default=None)   # "asc" | "desc" | None

class VirtualLibrary(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    resource_type: Literal["collection", "tag", "genre", "studio", "person", "all", "rsshub", "random"]
    resource_id: Optional[str] = None
    # image: Optional[str] = None  <-- 我们不再需要这个字段了，可以删除或注释掉
    image_tag: Optional[str] = None # <-- 【新增】用于存储图片的唯一标签
    rsshub_url: Optional[str] = None # <-- 【新增】RSSHUB链接
    rss_type: Optional[Literal["douban", "bangumi"]] = None # <-- 【新增】RSS类型
    rss_refresh_interval: Optional[int] = Field(default=None) # <-- 【新增】RSS库单独刷新间隔（小时）；未配置则使用全局 rss_refresh_interval
    fallback_tmdb_id: Optional[str] = None # <-- 【新增】RSS库的兜底TMDB ID
    fallback_tmdb_type: Optional[Literal["Movie", "TV"]] = None # <-- 【新增】RSS库的兜底TMDB类型
    enable_retention: bool = Field(default=False) # <-- 【新增】是否开启数据保留功能
    retention_days: int = Field(default=7) # <-- 【新增】RSS项目保留天数，默认7天
    advanced_filter_id: Optional[str] = None
    merge_by_tmdb_id: bool = Field(default=False)
    order: int = 0
    source_libraries: List[str] = Field(default_factory=list) # 源库范围限制，空列表表示不限制
    conditions: Optional[list] = None
    cover_custom_zh_font_path: Optional[str] = Field(default=None) # <-- 【新增】海报自定义中文字体
    cover_custom_en_font_path: Optional[str] = Field(default=None) # <-- 【新增】海报自定义英文字体
    cover_custom_image_path: Optional[str] = Field(default=None) # <-- 【新增】海报自定义图片目录
    cover_title_zh: Optional[str] = Field(default=None) # 海报中文标题（留空用虚拟库名称）
    cover_title_en: Optional[str] = Field(default=None) # 海报英文标题
    hidden: bool = Field(default=False) # True: 不参与 RSS 定时任务，且在 8999 代理上对 Items/Latest/视图隐藏

class WebhookSettings(BaseModel):
    """Emby Webhook：媒体变更后刷新「关联真实库」的虚拟库（全库型虚拟库不走 Webhook，仅定时）。"""
    enabled: bool = False
    # 非空则必须在请求头携带 X-Webhook-Secret: <secret>（或与之一致的 Bearer）
    secret: Optional[str] = Field(default=None)
    debounce_seconds: float = Field(default=90.0, ge=0.0, le=600.0)
    # 防抖分组：同一剧集 / 同一张专辑在窗口内多次事件只触发一次刷新
    group_by_series: bool = Field(default=True)
    group_by_album: bool = Field(default=True)
    on_item_added: bool = Field(default=True)
    on_item_removed: bool = Field(default=True)


class AppConfig(BaseModel):
    emby_url: str = Field(default="http://127.0.0.1:8096")
    emby_api_key: Optional[str] = Field(default="")
    emby_server_id: Optional[str] = Field(default=None) # 新增：用于TMDB缓存占位符的备用服务器ID
    log_level: Literal["debug", "info", "warn", "error"] = Field(default="info")
    display_order: List[str] = Field(default_factory=list)
    # Global ignore libraries list
    ignore_libraries: List[str] = Field(default_factory=list)

    # Global hide collection types
    hide: List[str] = Field(default_factory=list)
    
    # 使用别名 'library' 来兼容旧的 config.json
    virtual_libraries: List[VirtualLibrary] = Field(
        default_factory=list, 
        alias="library",
        validation_alias="library" # <-- 【新增】确保加载时也优先用 'library'
    )
    
    # 明确定义 advanced_filters，不使用任何复杂的配置
    advanced_filters: List[AdvancedFilter] = Field(default_factory=list)

    # 新增：缓存开关
    enable_cache: bool = Field(default=True)
    
    # 新增：自动生成封面的默认样式
    default_cover_style: str = Field(default='style_multi_1')

    # 新增：显示缺失剧集的开关
    show_missing_episodes: bool = Field(default=False)

    # 新增：TMDB API Key
    tmdb_api_key: Optional[str] = Field(default="")

    # 新增：TMDB HTTP 代理
    tmdb_proxy: Optional[str] = Field(default="")

    # 新增：RSS 定时刷新间隔（小时），0为禁用
    rss_refresh_interval: Optional[int] = Field(default=0)

    # Emby Webhook（管理 API /api/webhook/emby，需加入 Emby Premiere Webhook 目标 URL）
    webhook: WebhookSettings = Field(default_factory=WebhookSettings)

    # 新增：全局强制 TMDB ID 合并
    force_merge_by_tmdb_id: bool = Field(default=False)

    # 新增：自定义字体路径
    custom_zh_font_path: Optional[str] = Field(default="")
    custom_en_font_path: Optional[str] = Field(default="")
    custom_image_path: Optional[str] = Field(default="") # <-- 【新增】全局自定义图片目录

    class Config:
        # 允许从别名填充模型
        populate_by_name = True
