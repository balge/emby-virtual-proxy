import { defineStore } from "pinia";
import { useToast } from "@/composables/useToast";
import api from "../api";

const toast = useToast();

function effectiveResourceIds(lib) {
  const arr = lib?.resource_ids;
  if (Array.isArray(arr) && arr.length) return arr.map((x) => String(x));
  if (lib?.resource_id != null && String(lib.resource_id).trim())
    return [String(lib.resource_id).trim()];
  return [];
}

export const useMainStore = defineStore("main", {
  state: () => ({
    config: {
      // Legacy single-server fields (kept for backward compatibility with older backend)
      emby_url: "",
      emby_api_key: "",
      // Multi-server
      servers: [],
      admin_active_server_id: null,
      enable_cache: true,
      cache_refresh_interval: 12,
      hide: [],
      ignore_libraries: [],
      display_order: [],
      advanced_filters: [],
      library: [],
      real_libraries: [],
      webhook: { enabled: false, secret: null, delay_seconds: 0 },
    },
    originalConfigForComparison: null,
    classifications: {},
    saving: false,
    dataLoading: false,
    dataStatus: null,
    dialogVisible: false,
    isEditing: false,
    currentLibrary: {},
    allLibrariesForSorting: [],
    layoutManagerVisible: false,
    coverGenerating: false,
    switchingServer: false,
    personNameCache: {},
    /** 虚拟库行后台任务：'cover' | 'data'，key 为 library id 字符串 */
    libraryRowSyncing: {},
  }),

  getters: {
    virtualLibraries: (state) => state.config.library || [],
    serverOptions: (state) =>
      (state.config.servers || []).map((s) => ({
        value: s.id,
        label: `${s.name || "Emby"} (${s.proxy_port || "?"})`,
      })),
    activeServer: (state) => {
      const list = state.config.servers || [];
      if (!list.length) return null;
      const id = state.config.admin_active_server_id;
      if (id) {
        const found = list.find((s) => String(s.id) === String(id));
        if (found) return found;
      }
      return list.find((s) => s.enabled) || list[0];
    },

    sortedLibsInDisplayOrder: (state) => {
      if (!state.config.display_order || !state.allLibrariesForSorting.length)
        return [];
      const libMap = new Map(
        state.allLibrariesForSorting.map((lib) => [lib.id, lib]),
      );
      return state.config.display_order
        .map((id) => libMap.get(id))
        .filter(Boolean);
    },

    unsortedLibs: (state) => {
      if (!state.allLibrariesForSorting.length) return [];
      const sortedIds = new Set(state.config.display_order || []);
      return state.allLibrariesForSorting.filter(
        (lib) => !sortedIds.has(lib.id),
      );
    },

    availableResources: (state) => {
      const type = state.currentLibrary?.resource_type;
      if (!type || !state.classifications) return [];
      const pluralTypeMap = {
        collection: "collections",
        tag: "tags",
        genre: "genres",
        studio: "studios",
        person: "persons",
      };
      return state.classifications[pluralTypeMap[type]] || [];
    },
  },

  actions: {
    _defaultServerProfile() {
      return {
        enable_cache: true,
        display_order: [],
        ignore_libraries: [],
        real_libraries: [],
        real_library_cover_cron: null,
        hide: [],
        library: [],
        default_cover_style: "style_multi_1",
        show_missing_episodes: false,
        cache_refresh_interval: 12,
        webhook: { enabled: false, secret: null, delay_seconds: 0 },
        force_merge_by_tmdb_id: false,
      };
    },

    _ensureServersShape() {
      // Backend should already migrate, but keep frontend resilient.
      if (!Array.isArray(this.config.servers)) this.config.servers = [];
      if (
        !this.config.servers.length &&
        (this.config.emby_url || this.config.emby_api_key)
      ) {
        this.config.servers = [
          {
            id: crypto?.randomUUID ? crypto.randomUUID() : String(Date.now()),
            name: "Emby",
            emby_url: this.config.emby_url || "",
            emby_api_key: this.config.emby_api_key || "",
            enabled: true,
            proxy_port: 8999,
          },
        ];
      }
      if (!this.config.admin_active_server_id && this.config.servers.length) {
        this.config.admin_active_server_id = this.config.servers[0].id;
      }
      for (const s of this.config.servers) {
        if (!s.profile || typeof s.profile !== "object") s.profile = {};
        s.profile = { ...this._defaultServerProfile(), ...s.profile };
      }
    },

    _legacySnapshotForServerProfile() {
      return {
        enable_cache: this.config.enable_cache,
        display_order: Array.isArray(this.config.display_order)
          ? [...this.config.display_order]
          : [],
        ignore_libraries: Array.isArray(this.config.ignore_libraries)
          ? [...this.config.ignore_libraries]
          : [],
        real_libraries: Array.isArray(this.config.real_libraries)
          ? JSON.parse(JSON.stringify(this.config.real_libraries))
          : [],
        real_library_cover_cron: this.config.real_library_cover_cron ?? null,
        hide: Array.isArray(this.config.hide) ? [...this.config.hide] : [],
        library: Array.isArray(this.config.library)
          ? JSON.parse(JSON.stringify(this.config.library))
          : [],
        default_cover_style: this.config.default_cover_style,
        show_missing_episodes: this.config.show_missing_episodes,
        cache_refresh_interval: this.config.cache_refresh_interval,
        webhook: this.config.webhook
          ? JSON.parse(JSON.stringify(this.config.webhook))
          : { enabled: false, secret: null, delay_seconds: 0 },
        force_merge_by_tmdb_id: this.config.force_merge_by_tmdb_id,
      };
    },

    _applyServerProfileToLegacy(server) {
      const p = { ...this._defaultServerProfile(), ...(server?.profile || {}) };
      this.config.enable_cache = p.enable_cache ?? this.config.enable_cache;
      this.config.display_order = Array.isArray(p.display_order)
        ? [...p.display_order]
        : this.config.display_order || [];
      this.config.ignore_libraries = Array.isArray(p.ignore_libraries)
        ? [...p.ignore_libraries]
        : this.config.ignore_libraries || [];
      this.config.real_libraries = Array.isArray(p.real_libraries)
        ? JSON.parse(JSON.stringify(p.real_libraries))
        : this.config.real_libraries || [];
      this.config.real_library_cover_cron =
        p.real_library_cover_cron ?? this.config.real_library_cover_cron;
      this.config.hide = Array.isArray(p.hide)
        ? [...p.hide]
        : this.config.hide || [];
      this.config.library = Array.isArray(p.library)
        ? JSON.parse(JSON.stringify(p.library))
        : this.config.library || [];
      this.config.default_cover_style =
        p.default_cover_style ?? this.config.default_cover_style;
      this.config.show_missing_episodes =
        p.show_missing_episodes ?? this.config.show_missing_episodes;
      this.config.cache_refresh_interval =
        p.cache_refresh_interval ?? this.config.cache_refresh_interval;
      this.config.webhook = p.webhook
        ? JSON.parse(JSON.stringify(p.webhook))
        : this.config.webhook || {
            enabled: false,
            secret: null,
            delay_seconds: 0,
          };
      this.config.force_merge_by_tmdb_id =
        p.force_merge_by_tmdb_id ?? this.config.force_merge_by_tmdb_id;
    },

    async _loadAndApplyActiveServerProfile() {
      const sid = this.config.admin_active_server_id;
      if (!sid) return;
      try {
        const res = await api.getServerProfile(sid);
        const profile = res?.data || {};
        const server = (this.config.servers || []).find(
          (s) => String(s.id) === String(sid),
        );
        if (server)
          server.profile = { ...this._defaultServerProfile(), ...profile };
        this._applyServerProfileToLegacy({ profile });
      } catch (e) {
        this._handleApiError(e, "加载服务器配置失败");
      }
    },

    addServer() {
      this._ensureServersShape();
      const id = crypto?.randomUUID ? crypto.randomUUID() : String(Date.now());
      const usedPorts = new Set(
        (this.config.servers || []).map((s) => Number(s.proxy_port)),
      );
      let p = 8999;
      while (usedPorts.has(p)) p += 1;
      const created = {
        id,
        name: `Emby ${this.config.servers.length + 1}`,
        emby_url: "",
        emby_api_key: "",
        enabled: true,
        proxy_port: p,
      };
      this.config.servers.push(created);
      return created;
    },

    async createServer(
      serverInput,
      { switchToNew = false, persist = true } = {},
    ) {
      this._ensureServersShape();
      const id = crypto?.randomUUID ? crypto.randomUUID() : String(Date.now());
      const name =
        String(serverInput?.name || "").trim() ||
        `Emby ${this.config.servers.length + 1}`;
      const emby_url = String(serverInput?.emby_url || "").trim();
      const emby_api_key = String(serverInput?.emby_api_key || "").trim();
      const proxy_port = Number(serverInput?.proxy_port);
      const enabled = serverInput?.enabled !== false;

      if (
        !emby_url ||
        !emby_api_key ||
        !Number.isInteger(proxy_port) ||
        proxy_port < 1 ||
        proxy_port > 65535
      ) {
        throw new Error("服务器参数不完整或端口无效");
      }
      if (
        (this.config.servers || []).some(
          (s) => Number(s.proxy_port) === proxy_port,
        )
      ) {
        throw new Error(`代理端口重复：${proxy_port}`);
      }

      const created = {
        id,
        name,
        emby_url,
        emby_api_key,
        enabled,
        proxy_port,
        profile: this._defaultServerProfile(),
      };
      this.config.servers.push(created);

      if (switchToNew) {
        await this.setActiveServer(id);
      } else if (persist) {
        await api.updateConfig(this.config);
      }
      return created;
    },

    async updateServer(serverId, patch) {
      this._ensureServersShape();
      const list = this.config.servers || [];
      const idx = list.findIndex((s) => String(s.id) === String(serverId));
      if (idx < 0) throw new Error("服务器不存在");
      const current = list[idx];
      const next = {
        ...current,
        ...patch,
        name:
          String((patch?.name ?? current.name) || "").trim() ||
          current.name ||
          "Emby",
        emby_url: String((patch?.emby_url ?? current.emby_url) || "").trim(),
        emby_api_key: String(
          (patch?.emby_api_key ?? current.emby_api_key) || "",
        ).trim(),
        proxy_port: Number(patch?.proxy_port ?? current.proxy_port),
      };
      if (
        !next.emby_url ||
        !next.emby_api_key ||
        !Number.isInteger(next.proxy_port) ||
        next.proxy_port < 1 ||
        next.proxy_port > 65535
      ) {
        throw new Error("服务器参数不完整或端口无效");
      }
      if (
        list.some(
          (s, i) =>
            i !== idx && Number(s.proxy_port) === Number(next.proxy_port),
        )
      ) {
        throw new Error(`代理端口重复：${next.proxy_port}`);
      }
      list[idx] = next;
      await api.updateConfig(this.config);
      return next;
    },

    removeServer(serverId) {
      this._ensureServersShape();
      const list = this.config.servers || [];
      if (list.length <= 1) return false;
      const idx = list.findIndex((s) => String(s.id) === String(serverId));
      if (idx < 0) return false;
      list.splice(idx, 1);
      if (this.config.admin_active_server_id === serverId) {
        this.config.admin_active_server_id = list[0]?.id ?? null;
        if (list[0]) this._applyServerProfileToLegacy(list[0]);
      }
      return true;
    },

    async deleteServer(serverId) {
      if (String(serverId) === String(this.config.admin_active_server_id))
        return false;
      const ok = this.removeServer(serverId);
      if (!ok) return false;
      await api.updateConfig(this.config);
      return true;
    },

    async setActiveServer(serverId) {
      if (String(serverId) === String(this.config.admin_active_server_id))
        return;
      this.switchingServer = true;
      this._ensureServersShape();
      const current = (this.config.servers || []).find(
        (s) => String(s.id) === String(this.config.admin_active_server_id),
      );
      if (current) {
        current.profile = this._legacySnapshotForServerProfile();
        try {
          await api.updateServerProfile(current.id, current.profile);
        } catch (e) {
          this._handleApiError(e, "保存当前服务器配置失败");
        }
      }
      this.config.admin_active_server_id = serverId;
      // Persist selected admin context so backend APIs run against same server immediately.
      try {
        await api.updateConfig(this.config);
        const [classificationsRes, allLibsRes, configRes] = await Promise.all([
          api.getClassifications(),
          api.getAllLibraries(),
          api.getConfig(),
        ]);
        this.config = configRes.data;
        this._ensureServersShape();
        await this._loadAndApplyActiveServerProfile();
        this.classifications = classificationsRes.data;
        this.allLibrariesForSorting = allLibsRes.data;
        this.personNameCache = {};
        this.currentLibrary = {};
        this.resolveVisiblePersonNames();
        this.dataStatus = { type: "success", text: "已切换服务器并刷新数据" };
      } catch (error) {
        this._handleApiError(error, "切换服务器失败");
      } finally {
        this.switchingServer = false;
      }
    },

    async fetchAllInitialData() {
      this.dataLoading = true;
      this.dataStatus = null;
      try {
        const [configRes, classificationsRes, allLibsRes] = await Promise.all([
          api.getConfig(),
          api.getClassifications(),
          api.getAllLibraries(),
        ]);
        this.config = configRes.data;
        this._ensureServersShape();
        await this._loadAndApplyActiveServerProfile();
        if (!this.config.advanced_filters) this.config.advanced_filters = [];
        if (!this.config.library) this.config.library = [];
        if (!this.config.real_libraries) this.config.real_libraries = [];
        if (this.config.cache_refresh_interval == null)
          this.config.cache_refresh_interval = 12;
        if (!this.config.webhook)
          this.config.webhook = {
            enabled: false,
            secret: null,
            delay_seconds: 0,
          };

        this.originalConfigForComparison = JSON.parse(
          JSON.stringify(configRes.data),
        );
        this.classifications = classificationsRes.data;
        this.allLibrariesForSorting = allLibsRes.data;

        if (
          !this.config.display_order?.length &&
          this.allLibrariesForSorting.length
        ) {
          this.config.display_order = this.allLibrariesForSorting.map(
            (l) => l.id,
          );
        }
        this.resolveVisiblePersonNames();
        this.dataStatus = { type: "success", text: "Emby数据已加载" };
      } catch (error) {
        this._handleApiError(error, "加载初始数据失败");
        this.dataStatus = { type: "error", text: "Emby数据加载失败" };
      } finally {
        this.dataLoading = false;
      }
    },

    async _reloadConfigAndAllLibs() {
      try {
        const [configRes, allLibsRes] = await Promise.all([
          api.getConfig(),
          api.getAllLibraries(),
        ]);
        this.config = configRes.data;
        this._ensureServersShape();
        await this._loadAndApplyActiveServerProfile();
        if (!this.config.advanced_filters) this.config.advanced_filters = [];
        if (!this.config.library) this.config.library = [];
        this.originalConfigForComparison = JSON.parse(
          JSON.stringify(configRes.data),
        );
        this.allLibrariesForSorting = allLibsRes.data;
        this.resolveVisiblePersonNames();
      } catch (error) {
        this._handleApiError(error, "刷新配置列表失败");
      }
    },

    _sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    },

    /**
     * 后台 refresh 完成后会把新的 image_tag 写入配置；轮询 /config 直到 tag 变化或超时。
     * @returns {Promise<boolean>} true 表示检测到更新，false 为超时
     */
    async _pollLibraryDataReady(
      libraryId,
      previousImageTag,
      { maxMs = 120000, intervalMs = 2000 } = {},
    ) {
      const idStr = String(libraryId);
      const normalizedPrev = previousImageTag ?? null;
      const deadline = Date.now() + maxMs;
      while (Date.now() < deadline) {
        await this._sleep(intervalMs);
        try {
          const cfgRes = await api.getConfig();
          const lib = cfgRes.data?.library?.find((l) => String(l.id) === idStr);
          if (!lib) continue;
          const tag = lib.image_tag ?? null;
          if (tag && tag !== normalizedPrev) return true;
        } catch {
          /* 单次轮询失败则继续 */
        }
      }
      return false;
    },

    async saveAdvancedFilters(filters) {
      this.saving = true;
      try {
        await api.saveAdvancedFilters(filters);
        this.config.advanced_filters = filters;
      } catch (error) {
        this._handleApiError(error, "保存高级筛选器失败");
        throw error;
      } finally {
        this.saving = false;
      }
    },

    async generateLibraryCover(
      libraryId,
      titleZh,
      titleEn,
      styleName,
      tempImagePaths,
    ) {
      this.coverGenerating = true;
      try {
        const response = await api.generateCover(
          libraryId,
          titleZh,
          titleEn,
          styleName,
          tempImagePaths,
        );
        if (response.data?.success) {
          toast.success("封面已在后台生成！请点击保存。");
          if (this.currentLibrary?.id === libraryId) {
            this.currentLibrary.image_tag = response.data.image_tag;
          }
          return true;
        }
        return false;
      } catch (error) {
        this._handleApiError(error, "封面生成失败");
        return false;
      } finally {
        this.coverGenerating = false;
      }
    },

    async saveLibrary() {
      const lib = this.currentLibrary;
      if (!lib.name) {
        toast.warning("请填写所有必填字段");
        return;
      }
      if (lib.resource_type === "rsshub") {
        if (!lib.rsshub_url || !lib.rss_type) {
          toast.warning("请填写所有必填字段");
          return;
        }
      } else if (!["all", "random"].includes(lib.resource_type)) {
        const ids = effectiveResourceIds(lib);
        if (!ids.length) {
          toast.warning("请填写所有必填字段");
          return;
        }
      }

      if (!Array.isArray(lib.resource_ids)) lib.resource_ids = [];
      if (lib.resource_ids.length) {
        lib.resource_id = lib.resource_ids[0];
      } else if (!lib.resource_id) {
        lib.resource_id = null;
      }

      this.saving = true;
      try {
        const res = await (this.isEditing
          ? api.updateLibrary(lib.id, lib)
          : api.addLibrary(lib));
        const savedId = this.isEditing ? lib.id : res.data?.id;
        if (!savedId) {
          toast.error("保存失败：未返回虚拟库 ID");
          return;
        }
        toast.success(this.isEditing ? "虚拟库已更新" : "虚拟库已添加");
        this.dialogVisible = false;
        await this._reloadConfigAndAllLibs();

        const row = this.config.library?.find(
          (l) => String(l.id) === String(savedId),
        );
        const prevImageTag = row?.image_tag ?? null;
        const idStr = String(savedId);
        this.libraryRowSyncing[idStr] = "data";
        try {
          await api.refreshLibraryData(savedId);
          const ok = await this._pollLibraryDataReady(savedId, prevImageTag);
          if (!ok) {
            toast.warning(
              "后台仍在生成数据或封面，请稍后查看，或点击「数据」手动刷新",
            );
          }
        } catch (e) {
          this._handleApiError(e, "触发数据与封面刷新失败");
        } finally {
          await this._reloadConfigAndAllLibs();
          delete this.libraryRowSyncing[idStr];
          this.resolveVisiblePersonNames();
        }
      } catch (error) {
        this._handleApiError(error, "保存虚拟库失败");
      } finally {
        this.saving = false;
      }
    },

    resolveVisiblePersonNames() {
      if (!this.config.library) return;
      for (const lib of this.config.library.filter(
        (l) => l.resource_type === "person",
      )) {
        for (const pid of effectiveResourceIds(lib)) {
          this.resolvePersonName(pid);
        }
      }
    },

    async fetchAllEmbyData() {
      this.dataLoading = true;
      this.dataStatus = { type: "info", text: "正在刷新..." };
      try {
        const [classificationsRes, allLibsRes] = await Promise.all([
          api.getClassifications(),
          api.getAllLibraries(),
        ]);
        this.classifications = classificationsRes.data;
        this.allLibrariesForSorting = allLibsRes.data;
        this.dataStatus = { type: "success", text: "Emby数据已刷新" };
        toast.success("分类和媒体库数据已从Emby刷新！");
      } catch (error) {
        this._handleApiError(error, "刷新Emby数据失败");
        this.dataStatus = { type: "error", text: "刷新失败" };
      } finally {
        this.dataLoading = false;
      }
    },

    async refreshClassificationsOnly() {
      try {
        const res = await api.getClassifications();
        this.classifications = res.data;
      } catch (error) {
        this._handleApiError(error, "刷新分类数据失败");
      }
    },

    async saveDisplayOrder(orderedIds) {
      this.saving = true;
      try {
        this.config.display_order = orderedIds;
        await api.saveDisplayOrder(this.config.display_order);
        toast.success("主页布局已保存！");
        await this._reloadConfigAndAllLibs();
      } catch (error) {
        this._handleApiError(error, "保存布局失败");
      } finally {
        this.saving = false;
      }
    },

    openLayoutManager() {
      this.layoutManagerVisible = true;
    },

    async deleteLibrary(id) {
      try {
        await api.deleteLibrary(id);
        toast.success("虚拟库已删除");
        await this._reloadConfigAndAllLibs();
      } catch (error) {
        this._handleApiError(error, "删除虚拟库失败");
      }
    },

    async toggleLibraryHidden(id) {
      try {
        const res = await api.toggleLibraryHidden(id);
        toast.success(res.data.hidden ? "虚拟库已隐藏" : "虚拟库已恢复显示");
        await this._reloadConfigAndAllLibs();
      } catch (error) {
        this._handleApiError(error, "切换隐藏状态失败");
      }
    },

    async refreshRssLibrary(id) {
      try {
        await api.refreshRssLibrary(id);
        toast.info(
          "RSS 刷新已提交。拉取与入库较慢，可能要等几分钟，请稍后在客户端或刷新本页查看。",
        );
        await this._reloadConfigAndAllLibs();
      } catch (e) {
        this._handleApiError(e, "刷新 RSS 库失败");
      }
    },
    async refreshLibraryCover(id) {
      const idStr = String(id);
      const lib = this.config.library?.find((l) => String(l.id) === idStr);
      const prevImageTag = lib?.image_tag ?? null;
      this.libraryRowSyncing[idStr] = "cover";
      try {
        await api.refreshLibraryCover(id);
        const ok = await this._pollLibraryDataReady(idStr, prevImageTag);
        if (ok) toast.success("封面已更新");
        else toast.warning("封面仍在生成，请稍后查看");
      } catch (e) {
        this._handleApiError(e, "刷新封面失败");
      } finally {
        await this._reloadConfigAndAllLibs();
        delete this.libraryRowSyncing[idStr];
        this.resolveVisiblePersonNames();
      }
    },
    async refreshLibraryData(id) {
      const idStr = String(id);
      const lib = this.config.library?.find((l) => String(l.id) === idStr);
      const prevImageTag = lib?.image_tag ?? null;
      this.libraryRowSyncing[idStr] = "data";
      try {
        await api.refreshLibraryData(id);
        const ok = await this._pollLibraryDataReady(idStr, prevImageTag);
        if (ok) toast.success("数据与封面已更新");
        else toast.warning("后台仍在处理，请稍后查看或再次点击「数据」");
      } catch (e) {
        this._handleApiError(e, "刷新数据失败");
      } finally {
        await this._reloadConfigAndAllLibs();
        delete this.libraryRowSyncing[idStr];
        this.resolveVisiblePersonNames();
      }
    },
    async refreshAllCovers() {
      try {
        await api.refreshAllCovers();
        toast.success("所有虚拟库封面刷新已启动");
      } catch (e) {
        this._handleApiError(e, "刷新所有封面失败");
      }
    },
    async restartProxyServer() {
      this.saving = true;
      try {
        await api.restartProxy();
        toast.success("代理服务重启命令已发送！");
      } catch (e) {
        this._handleApiError(e, "重启代理服务失败");
      } finally {
        this.saving = false;
      }
    },
    async clearAllCovers() {
      this.saving = true;
      try {
        await api.clearCovers();
        toast.success("所有本地封面已清空！");
        await this._reloadConfigAndAllLibs();
      } catch (e) {
        this._handleApiError(e, "清空封面失败");
      } finally {
        this.saving = false;
      }
    },
    async saveConfig() {
      this.saving = true;
      try {
        const current = this.activeServer;
        if (current) current.profile = this._legacySnapshotForServerProfile();
        const ports = new Set();
        for (const s of this.config.servers || []) {
          const p = Number(s.proxy_port);
          if (!Number.isInteger(p) || p < 1 || p > 65535) {
            toast.warning(`服务器 ${s.name || ""} 端口无效`);
            this.saving = false;
            return;
          }
          if (ports.has(p)) {
            toast.warning(`代理端口重复：${p}`);
            this.saving = false;
            return;
          }
          ports.add(p);
        }
        await api.updateConfig(this.config);
        if (current) {
          await api.updateServerProfile(current.id, current.profile);
        }
        toast.success("系统设置已保存");
        this.originalConfigForComparison = JSON.parse(
          JSON.stringify(this.config),
        );
      } catch (e) {
        this._handleApiError(e, "保存设置失败");
      } finally {
        this.saving = false;
      }
    },

    openAddDialog() {
      this.isEditing = false;
      this.currentLibrary = {
        name: "",
        resource_type: "collection",
        resource_id: "",
        resource_ids: [],
        merge_by_tmdb_id: false,
        image_tag: null,
        fallback_tmdb_id: null,
        fallback_tmdb_type: null,
        cache_refresh_interval: null,
        source_libraries: [],
      };
      this.dialogVisible = true;
    },
    openEditDialog(library) {
      this.isEditing = true;
      this.currentLibrary = JSON.parse(JSON.stringify(library));
      if (this.currentLibrary.merge_by_tmdb_id === undefined)
        this.currentLibrary.merge_by_tmdb_id = false;
      if (!Array.isArray(this.currentLibrary.resource_ids))
        this.currentLibrary.resource_ids = [];
      if (
        !this.currentLibrary.resource_ids.length &&
        this.currentLibrary.resource_id
      ) {
        this.currentLibrary.resource_ids = [this.currentLibrary.resource_id];
      }
      if (this.currentLibrary.resource_type === "person") {
        for (const pid of effectiveResourceIds(this.currentLibrary)) {
          this.resolvePersonName(pid);
        }
      }
      this.dialogVisible = true;
    },

    _handleApiError(error, messagePrefix) {
      const status = error.response?.status;
      const detail = error.response?.data?.detail;
      // 409：资源冲突（如 RSS 已有任务），用提示而非「失败」口吻
      if (status === 409) {
        const msg =
          typeof detail === "string" && detail.trim()
            ? detail
            : "当前已有任务在执行，请稍后再试。";
        toast.info(msg);
        return;
      }
      toast.error(`${messagePrefix}: ${detail || "请检查网络或联系管理员"}`);
    },

    async resolvePersonName(personId) {
      if (!personId || this.personNameCache[personId]) return;
      this.personNameCache[personId] = "...";
      try {
        const response = await api.resolveItem(personId);
        this.personNameCache[personId] = response.data.name;
      } catch {
        this.personNameCache[personId] = "未知";
      }
    },

    // Real Libraries
    async syncRealLibraries() {
      try {
        const res = await api.syncRealLibraries();
        this.config.real_libraries = res.data;
        toast.success("已从 Emby 同步真实库列表");
      } catch (e) {
        this._handleApiError(e, "同步真实库失败");
      }
    },
    async saveRealLibraries() {
      this.saving = true;
      try {
        await api.saveRealLibraries(
          this.config.real_libraries,
          this.config.real_library_cover_cron,
        );
        toast.success("真实库配置已保存");
      } catch (e) {
        this._handleApiError(e, "保存真实库配置失败");
      } finally {
        this.saving = false;
      }
    },
    async refreshRealLibraryCover(id) {
      try {
        const res = await api.refreshRealLibraryCover(id);
        if (res.data.ok) {
          toast.success("封面刷新成功");
          const rl = this.config.real_libraries?.find((r) => r.id === id);
          if (rl) rl.image_tag = res.data.image_tag;
        } else toast.warning("封面生成失败");
      } catch (e) {
        this._handleApiError(e, "刷新真实库封面失败");
      }
    },
    async refreshAllRealLibraryCovers() {
      try {
        await api.refreshAllRealLibraryCovers();
        toast.success("全部真实库封面刷新已启动");
      } catch (e) {
        this._handleApiError(e, "刷新全部封面失败");
      }
    },
  },
});
