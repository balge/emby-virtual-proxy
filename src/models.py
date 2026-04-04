# src/models.py (Final Corrected Version)

from pydantic import BaseModel, Field, ConfigDict
from typing import List, Literal, Optional
import uuid


def _norm_id_list(ids: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    if not ids:
        return out
    for x in ids:
        if x is None:
            continue
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out

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
    """兼容旧配置；新逻辑优先用 resource_ids。保存时仍可写入首项便于旧代码。"""
    resource_ids: List[str] = Field(default_factory=list)
    # image: Optional[str] = None  <-- 我们不再需要这个字段了，可以删除或注释掉
    image_tag: Optional[str] = None # <-- 【新增】用于存储图片的唯一标签
    rsshub_url: Optional[str] = None # <-- 【新增】RSSHUB链接
    rss_type: Optional[Literal["douban", "bangumi"]] = None # <-- 【新增】RSS类型
    cache_refresh_interval: Optional[int] = Field(default=None) # 统一刷新间隔（小时）；留空走全局
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

    def resolved_resource_ids(self) -> List[str]:
        """合集/标签/类型/工作室/人员 等可多项；并集语义。旧数据仅 resource_id 时视为单元素。"""
        ids = _norm_id_list(self.resource_ids)
        if ids:
            return ids
        if self.resource_id is not None and str(self.resource_id).strip():
            return [str(self.resource_id).strip()]
        return []

class RealLibraryConfig(BaseModel):
    """真实媒体库配置：启用/禁用、封面标题"""
    id: str  # Emby 真实库 ID
    name: str  # 从 Emby 同步的库名称
    enabled: bool = Field(default=True)  # True=启用（可被源库选择），False=忽略
    cover_enabled: bool = Field(default=True)  # True=参与封面生成，False=跳过
    cover_title_zh: Optional[str] = Field(default=None)  # 封面中文标题
    cover_title_en: Optional[str] = Field(default=None)  # 封面英文标题
    image_tag: Optional[str] = Field(default=None)  # 封面 image tag

class WebhookSettings(BaseModel):
    """Emby Webhook：仅对 resource_type=all 且源库范围命中变更库的虚拟库触发刷新。"""
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    secret: Optional[str] = Field(default=None)
    delay_seconds: int = Field(default=0)  # 延迟合并窗口（秒），默认0立即刷新


class AppConfig(BaseModel):
    emby_url: str = Field(default="http://127.0.0.1:8096")
    emby_api_key: Optional[str] = Field(default="")
    emby_server_id: Optional[str] = Field(default=None) # 新增：用于TMDB缓存占位符的备用服务器ID
    log_level: Literal["debug", "info", "warn", "error"] = Field(default="info")
    display_order: List[str] = Field(default_factory=list)
    # Global ignore libraries list (deprecated, migrated to real_libraries.enabled)
    ignore_libraries: List[str] = Field(default_factory=list)

    # 真实媒体库配置
    real_libraries: List[RealLibraryConfig] = Field(default_factory=list)

    # 真实库封面刷新 cron 表达式，如 "0 3 * * *"
    real_library_cover_cron: Optional[str] = Field(default=None)

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

    # 新增：自动生成封面的默认样式
    default_cover_style: str = Field(default='style_multi_1')

    # 新增：显示缺失剧集的开关
    show_missing_episodes: bool = Field(default=False)

    # 新增：TMDB API Key
    tmdb_api_key: Optional[str] = Field(default="")

    # 新增：TMDB HTTP 代理
    tmdb_proxy: Optional[str] = Field(default="")

    # 统一刷新间隔（小时）；用于 RSS 定时调度，也作为 random/分页缓存默认 TTL
    cache_refresh_interval: Optional[int] = Field(default=12)

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
        # 兼容旧配置文件：旧版可能包含 enable_cache 等字段
        extra = "ignore"

    @property
    def disabled_library_ids(self) -> set:
        """获取被禁用的真实库 ID 集合。"""
        return {rl.id for rl in self.real_libraries if not rl.enabled}
