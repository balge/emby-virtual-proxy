# src/models.py (Final Corrected Version)

from pydantic import BaseModel, Field, ConfigDict
from typing import Dict, List, Literal, Optional
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
    cover_image_tags: Dict[str, str] = Field(default_factory=dict)
    rsshub_url: Optional[str] = None # <-- 【新增】RSSHUB链接
    rss_type: Optional[Literal["douban", "bangumi"]] = None # <-- 【新增】RSS类型
    cache_refresh_interval: Optional[int] = Field(
        default=None,
    )  # 小时；留空走全局。控制磁盘列表缓存 TTL 与定时重拉（非 RSS）
    fallback_tmdb_id: Optional[str] = None # <-- 【新增】RSS库的兜底TMDB ID
    fallback_tmdb_type: Optional[Literal["Movie", "TV"]] = None # <-- 【新增】RSS库的兜底TMDB类型
    enable_retention: bool = Field(default=False) # <-- 【新增】是否开启数据保留功能
    retention_days: int = Field(default=7) # <-- 【新增】RSS项目保留天数，默认7天
    advanced_filter_id: Optional[str] = None
    merge_by_tmdb_id: bool = Field(default=False)
    order: int = 0
    source_libraries: List[str] = Field(default_factory=list) # 源库范围限制，空列表表示不限制
    # 虚拟库分级过滤：配置后隐藏该分级及以上内容（基于 Emby /OfficialRatings 顺序）
    random_hide_rating_and_above: Optional[str] = Field(default=None)
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


class EmbyServerConfig(BaseModel):
    """
    One upstream Emby server + one proxy listen port.

    Notes:
    - proxy_port is the port clients connect to on the proxy container/host.
      Docker publish (compose ports) must include the same port mapping manually.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(default="Emby")
    emby_url: str = Field(default="http://127.0.0.1:8096")
    emby_api_key: Optional[str] = Field(default="")
    enabled: bool = Field(default=True)
    proxy_port: int = Field(default=8999)
    # Optional: used by TMDB/RSS placeholders in some paths; kept for compatibility.
    emby_server_id: Optional[str] = Field(default=None)
    # Per-server scoped settings/profile (payload dict for backward-compatible gradual rollout)
    profile: dict = Field(default_factory=dict)


class AppConfig(BaseModel):
    # ---- Legacy single-server fields (migrated into servers[] on load) ----
    emby_url: Optional[str] = Field(default=None)
    emby_api_key: Optional[str] = Field(default=None)
    emby_server_id: Optional[str] = Field(default=None)  # legacy fallback server_id

    # ---- Multi-server ----
    servers: List[EmbyServerConfig] = Field(default_factory=list)
    admin_active_server_id: Optional[str] = Field(default=None)

    log_level: Literal["debug", "info", "warn", "error"] = Field(default="info")
    # 代理是否缓存部分 Emby JSON（GET）；关闭后直连 Emby、不写入 api_cache
    enable_cache: bool = Field(default=True)
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
    # 封面样式模式：静态、动态 GIF、动态 APNG
    cover_style_variant: Literal["static", "animated", "animated_apng"] = Field(default="static")
    # 动图参数
    animation_duration: int = Field(default=8)
    animation_fps: int = Field(default=24)
    animated_image_count: int = Field(default=6)
    animated_departure_type: Literal["fly", "fade", "crossfade"] = Field(default="fly")
    animated_scroll_direction: Literal["up", "down", "alternate", "alternate_reverse"] = Field(default="alternate")

    # 新增：显示缺失剧集的开关
    show_missing_episodes: bool = Field(default=False)

    # 新增：TMDB API Key
    tmdb_api_key: Optional[str] = Field(default="")

    # 新增：TMDB HTTP 代理
    tmdb_proxy: Optional[str] = Field(default="")

    # 统一刷新间隔（小时）：RSS 定时；其它走磁盘缓存的虚拟库的 TTL 与 disk_refresh 定时
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
        # 兼容旧配置文件中的未知字段
        extra = "ignore"

    def ensure_servers_migrated(self) -> None:
        """
        In-place migrate legacy single-server fields into servers[].
        Safe to call multiple times.
        """
        if self.servers:
            # Ensure admin_active_server_id is set to an existing enabled server.
            if not self.admin_active_server_id:
                first_enabled = next((s for s in self.servers if s.enabled), None)
                self.admin_active_server_id = first_enabled.id if first_enabled else self.servers[0].id
            self._ensure_server_profiles_initialized()
            # Keep legacy single-server fields in sync with current admin selection
            active = self._select_active_server_no_ensure()
            if active:
                self.emby_url = active.emby_url
                self.emby_api_key = active.emby_api_key
                self.emby_server_id = active.emby_server_id or self.emby_server_id
            # Project current active server profile into legacy top-level fields so old code keeps working.
            self.sync_active_profile_to_legacy(active)
            return

        # Build server[0] from legacy fields (or defaults).
        url = (self.emby_url or "http://127.0.0.1:8096").strip()
        key = (self.emby_api_key or "").strip()
        s = EmbyServerConfig(
            name="Emby",
            emby_url=url,
            emby_api_key=key,
            enabled=True,
            proxy_port=8999,
            emby_server_id=self.emby_server_id,
        )
        self.servers = [s]
        self.admin_active_server_id = s.id
        self._ensure_server_profiles_initialized()
        # Sync legacy fields for old code paths
        self.emby_url = s.emby_url
        self.emby_api_key = s.emby_api_key
        self.emby_server_id = s.emby_server_id or self.emby_server_id
        self.sync_active_profile_to_legacy(s)

    def _select_active_server_no_ensure(self) -> Optional[EmbyServerConfig]:
        if not self.servers:
            return None
        if self.admin_active_server_id:
            found = next((s for s in self.servers if s.id == self.admin_active_server_id), None)
            if found:
                return found
        return next((s for s in self.servers if s.enabled), self.servers[0])

    def _profile_snapshot_from_legacy(self) -> dict:
        return {
            "enable_cache": self.enable_cache,
            "display_order": list(self.display_order or []),
            "ignore_libraries": list(self.ignore_libraries or []),
            "real_libraries": [rl.model_dump() for rl in (self.real_libraries or [])],
            "real_library_cover_cron": self.real_library_cover_cron,
            "hide": list(self.hide or []),
            "library": [v.model_dump() for v in (self.virtual_libraries or [])],
            "default_cover_style": self.default_cover_style,
            "cover_style_variant": self.cover_style_variant,
            "animation_duration": self.animation_duration,
            "animation_fps": self.animation_fps,
            "animated_image_count": self.animated_image_count,
            "animated_departure_type": self.animated_departure_type,
            "animated_scroll_direction": self.animated_scroll_direction,
            "show_missing_episodes": self.show_missing_episodes,
            "cache_refresh_interval": self.cache_refresh_interval,
            "webhook": self.webhook.model_dump() if self.webhook else {},
            "force_merge_by_tmdb_id": self.force_merge_by_tmdb_id,
        }

    def _default_profile_template(self) -> dict:
        return {
            "enable_cache": True,
            "display_order": [],
            "ignore_libraries": [],
            "real_libraries": [],
            "real_library_cover_cron": None,
            "hide": [],
            "library": [],
            "default_cover_style": "style_multi_1",
            "cover_style_variant": "static",
            "animation_duration": 8,
            "animation_fps": 24,
            "animated_image_count": 6,
            "animated_departure_type": "fly",
            "animated_scroll_direction": "alternate",
            "show_missing_episodes": False,
            "cache_refresh_interval": 12,
            "webhook": WebhookSettings().model_dump(),
            "force_merge_by_tmdb_id": False,
        }

    def _ensure_server_profiles_initialized(self) -> None:
        base = self._default_profile_template()
        for s in self.servers:
            if not isinstance(s.profile, dict):
                s.profile = {}
            for k, v in base.items():
                if k not in s.profile:
                    s.profile[k] = v

    def sync_active_profile_to_legacy(self, active: Optional[EmbyServerConfig] = None) -> None:
        """
        Load active server scoped settings into legacy top-level fields.
        This keeps old call sites and APIs working while introducing per-server profiles.
        """
        if active is None:
            # Avoid recursion: do not call ensure_servers_migrated() here.
            active = self._select_active_server_no_ensure()
        if not active or not isinstance(active.profile, dict):
            return
        p = {**self._default_profile_template(), **active.profile}
        self.enable_cache = bool(p.get("enable_cache", self.enable_cache))
        self.display_order = list(p.get("display_order", self.display_order or []))
        self.ignore_libraries = list(p.get("ignore_libraries", self.ignore_libraries or []))
        self.real_libraries = [
            RealLibraryConfig.model_validate(x) for x in (p.get("real_libraries") or [])
        ]
        self.real_library_cover_cron = p.get("real_library_cover_cron", self.real_library_cover_cron)
        self.hide = list(p.get("hide", self.hide or []))
        self.virtual_libraries = [
            VirtualLibrary.model_validate(x) for x in (p.get("library") or p.get("virtual_libraries") or [])
        ]
        self.default_cover_style = p.get("default_cover_style", self.default_cover_style)
        self.cover_style_variant = p.get("cover_style_variant", self.cover_style_variant)
        self.animation_duration = int(p.get("animation_duration", self.animation_duration) or self.animation_duration)
        self.animation_fps = int(p.get("animation_fps", self.animation_fps) or self.animation_fps)
        self.animated_image_count = int(p.get("animated_image_count", self.animated_image_count) or self.animated_image_count)
        self.animated_departure_type = p.get("animated_departure_type", self.animated_departure_type)
        self.animated_scroll_direction = p.get("animated_scroll_direction", self.animated_scroll_direction)
        self.show_missing_episodes = bool(p.get("show_missing_episodes", self.show_missing_episodes))
        self.cache_refresh_interval = p.get("cache_refresh_interval", self.cache_refresh_interval)
        self.webhook = WebhookSettings.model_validate(p.get("webhook") or self.webhook.model_dump())
        self.force_merge_by_tmdb_id = bool(p.get("force_merge_by_tmdb_id", self.force_merge_by_tmdb_id))

    def sync_legacy_to_active_profile(self) -> None:
        """
        Save current top-level (legacy) settings into active server profile.
        Call this before save_config.
        """
        self.ensure_servers_migrated()
        active = self._select_active_server_no_ensure()
        if not active:
            return
        active.profile = self._profile_snapshot_from_legacy()

    def get_admin_active_server(self) -> Optional[EmbyServerConfig]:
        self.ensure_servers_migrated()
        return self._select_active_server_no_ensure()

    def get_server_by_proxy_port(self, port: int) -> Optional[EmbyServerConfig]:
        self.ensure_servers_migrated()
        return next((s for s in self.servers if int(s.proxy_port) == int(port) and s.enabled), None)

    def get_server_by_id(self, server_id: str) -> Optional[EmbyServerConfig]:
        self.ensure_servers_migrated()
        sid = str(server_id)
        return next((s for s in self.servers if str(s.id) == sid), None)

    def get_server_profile(self, server_id: str) -> dict:
        server = self.get_server_by_id(server_id)
        if not server:
            return self._default_profile_template()
        p = server.profile if isinstance(server.profile, dict) else {}
        return {**self._default_profile_template(), **p}

    def set_server_profile(self, server_id: str, profile: dict) -> bool:
        server = self.get_server_by_id(server_id)
        if not server:
            return False
        merged = {**self._default_profile_template(), **(profile or {})}
        server.profile = merged
        return True

    def list_enabled_proxy_ports(self) -> List[int]:
        self.ensure_servers_migrated()
        ports: List[int] = []
        for s in self.servers:
            if not s.enabled:
                continue
            p = int(s.proxy_port)
            if p not in ports:
                ports.append(p)
        return ports

    @property
    def disabled_library_ids(self) -> set:
        """获取被禁用的真实库 ID 集合。"""
        return {rl.id for rl in self.real_libraries if not rl.enabled}
