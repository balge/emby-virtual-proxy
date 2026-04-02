// frontend/src/stores/main.js (最终修复版)

import { defineStore } from 'pinia';
import { ElMessage } from 'element-plus';
import api from '../api';

export const useMainStore = defineStore('main', {
  state: () => ({
    config: { 
        emby_url: '', 
        emby_api_key: '', 
        cache_refresh_interval: 12,
        hide: [], 
        ignore_libraries: [],
        display_order: [], 
        advanced_filters: [],
        library: [], // 确保 config 对象中有 library 数组
        real_libraries: [], // 真实库配置
        webhook: { enabled: false, secret: null },
    },
    originalConfigForComparison: null,
    // 【核心修复】: 删除独立的 virtualLibraries 状态，它应该始终是 config 的一部分
    // virtualLibraries: [], 
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
    personNameCache: {},
  }),

  getters: {
    // 【核心修复】: 直接从 config.library 读取数据
    virtualLibraries: (state) => state.config.library || [],

    sortedLibsInDisplayOrder: (state) => {
        if (!state.config.display_order || !state.allLibrariesForSorting.length) {
            return [];
        }
        const libMap = new Map(state.allLibrariesForSorting.map(lib => [lib.id, lib]));
        return state.config.display_order
            .map(id => libMap.get(id))
            .filter(Boolean);
    },
    unsortedLibs: (state) => {
        if (!state.allLibrariesForSorting.length) return [];
        const sortedIds = new Set(state.config.display_order || []);
        return state.allLibrariesForSorting.filter(lib => !sortedIds.has(lib.id));
    },
    availableResources: (state) => {
      const type = state.currentLibrary?.resource_type;
      if (!type || !state.classifications) return [];
      const pluralTypeMap = {
        collection: 'collections',
        tag: 'tags',
        genre: 'genres',
        studio: 'studios',
        person: 'persons'
      };
      return state.classifications[pluralTypeMap[type]] || [];
    },
  },

  actions: {
    async fetchAllInitialData() {
      this.dataLoading = true;
      this.dataStatus = null;
      try {
        const [configRes, classificationsRes, allLibsRes] = await Promise.all([
          api.getConfig(),
          api.getClassifications(),
          api.getAllLibraries(),
        ]);
        
        // 直接将获取到的配置赋值给 state.config
        this.config = configRes.data;
        if (!this.config.advanced_filters) this.config.advanced_filters = [];
        if (!this.config.library) this.config.library = []; // 确保 library 数组存在
        if (!this.config.real_libraries) this.config.real_libraries = [];
        if (this.config.cache_refresh_interval === undefined || this.config.cache_refresh_interval === null) {
            this.config.cache_refresh_interval = 12;
        }
        if (!this.config.webhook) {
            this.config.webhook = { enabled: false, secret: null };
        }

        this.originalConfigForComparison = JSON.parse(JSON.stringify(configRes.data));
        this.classifications = classificationsRes.data;
        this.allLibrariesForSorting = allLibsRes.data;

        if (!this.config.display_order || this.config.display_order.length === 0) {
            if (this.allLibrariesForSorting.length > 0) {
                this.config.display_order = this.allLibrariesForSorting.map(l => l.id);
            }
        }
        this.resolveVisiblePersonNames();
        this.dataStatus = { type: 'success', text: 'Emby数据已加载' };
      } catch (error) {
        this._handleApiError(error, '加载初始数据失败');
        this.dataStatus = { type: 'error', text: 'Emby数据加载失败' };
      } finally {
        this.dataLoading = false;
      }
    },

    async _reloadConfigAndAllLibs() {
        try {
            const [configRes, allLibsRes] = await Promise.all([
                api.getConfig(),
                api.getAllLibraries()
            ]);
            this.config = configRes.data;
            if (!this.config.advanced_filters) this.config.advanced_filters = [];
            if (!this.config.library) this.config.library = [];

            this.originalConfigForComparison = JSON.parse(JSON.stringify(configRes.data));
            this.allLibrariesForSorting = allLibsRes.data;
            this.resolveVisiblePersonNames();
        } catch (error) {
            this._handleApiError(error, "刷新配置列表失败");
        }
    },

    // --- 所有其他 actions 保持和上一版一样 ---
    // 为了确保万无一失，我将它们全部粘贴在这里

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
    
    async generateLibraryCover(libraryId, titleZh, titleEn, styleName, tempImagePaths) {
        this.coverGenerating = true;
        try {
            const response = await api.generateCover(libraryId, titleZh, titleEn, styleName, tempImagePaths);
            if (response.data && response.data.success) {
                ElMessage.success("封面已在后台生成！请点击保存。");
                const newImageTag = response.data.image_tag;
                if (this.currentLibrary && this.currentLibrary.id === libraryId) {
                    this.currentLibrary.image_tag = newImageTag;
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
      const libraryToSave = this.currentLibrary;

      // 采用更清晰的分步验证逻辑来彻底修复问题
      if (!libraryToSave.name) {
        ElMessage.warning('请填写所有必填字段');
        return;
      }

      if (libraryToSave.resource_type === 'rsshub') {
        if (!libraryToSave.rsshub_url || !libraryToSave.rss_type) {
          ElMessage.warning('请填写所有必填字段');
          return;
        }
      } else if (libraryToSave.resource_type !== 'all' && libraryToSave.resource_type !== 'random') {
        if (!libraryToSave.resource_id) {
          ElMessage.warning('请填写所有必填字段');
          return;
        }
      }

      this.saving = true;
      const action = this.isEditing ? api.updateLibrary(libraryToSave.id, libraryToSave) : api.addLibrary(libraryToSave);
      const successMsg = this.isEditing ? '虚拟库已更新' : '虚拟库已添加';
      try {
        await action;
        ElMessage.success(successMsg);
        this.dialogVisible = false;
        await this._reloadConfigAndAllLibs();
      } catch (error) {
        this._handleApiError(error, '保存虚拟库失败');
      } finally {
        this.saving = false;
      }
    },
    
    resolveVisiblePersonNames() {
        if (!this.config.library) return;
        const personLibs = this.config.library.filter(lib => lib.resource_type === 'person');
        for (const lib of personLibs) {
            if (lib.resource_id) { this.resolvePersonName(lib.resource_id); }
        }
    },
    
    async fetchAllEmbyData() {
        this.dataLoading = true;
        this.dataStatus = { type: 'info', text: '正在刷新...' };
        try {
            const [classificationsRes, allLibsRes] = await Promise.all([
                api.getClassifications(),
                api.getAllLibraries(),
            ]);
            this.classifications = classificationsRes.data;
            this.allLibrariesForSorting = allLibsRes.data;
            this.dataStatus = { type: 'success', text: 'Emby数据已刷新' };
            ElMessage.success("分类和媒体库数据已从Emby刷新！");
        } catch (error) {
            this._handleApiError(error, '刷新Emby数据失败');
            this.dataStatus = { type: 'error', text: '刷新失败' };
        } finally {
            this.dataLoading = false;
        }
    },

    async saveDisplayOrder(orderedIds) {
        this.saving = true;
        try {
            // 直接修改 state.config.display_order
            this.config.display_order = orderedIds;
            // 然后将整个 config 对象保存
            await api.saveDisplayOrder(this.config.display_order);
            ElMessage.success("主页布局已保存！");
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
            ElMessage.success('虚拟库已删除');
            await this._reloadConfigAndAllLibs();
        } catch (error) {
            this._handleApiError(error, '删除虚拟库失败');
        }
    },

    async toggleLibraryHidden(id) {
        try {
            const res = await api.toggleLibraryHidden(id);
            const hidden = res.data.hidden;
            ElMessage.success(hidden ? '虚拟库已隐藏：代理端不再展示，且跳过 RSS 定时更新' : '虚拟库已恢复显示');
            await this._reloadConfigAndAllLibs();
        } catch (error) {
            this._handleApiError(error, '切换隐藏状态失败');
        }
    },

    async refreshRssLibrary(id) {
        try {
            await api.refreshRssLibrary(id);
            ElMessage.success('RSS 库刷新请求已发送，将在后台处理。');
        } catch (error) {
            this._handleApiError(error, '刷新 RSS 库失败');
        }
    },
    async refreshLibraryCover(id) {
        try {
            await api.refreshLibraryCover(id);
            ElMessage.success('封面刷新已启动，将在后台处理。');
        } catch (error) {
            this._handleApiError(error, '刷新封面失败');
        }
    },
    async refreshLibraryData(id) {
        try {
            await api.refreshLibraryData(id);
            ElMessage.success('数据和封面刷新已启动，将在后台处理。');
        } catch (error) {
            this._handleApiError(error, '刷新数据失败');
        }
    },
    async refreshAllCovers() {
        try {
            await api.refreshAllCovers();
            ElMessage.success('所有虚拟库封面刷新已启动，将在后台处理。');
        } catch (error) {
            this._handleApiError(error, '刷新所有封面失败');
        }
    },
    async restartProxyServer() {
        this.saving = true;
        try {
            await api.restartProxy();
            ElMessage.success("代理服务重启命令已发送！它将在几秒后恢复服务。");
        } catch (error) {
            this._handleApiError(error, "重启代理服务失败");
        } finally {
            this.saving = false;
        }
    },

    async clearAllCovers() {
        this.saving = true;
        try {
            await api.clearCovers();
            ElMessage.success("所有本地封面已清空！");
            // 刷新配置以更新UI（清除image_tag）
            await this._reloadConfigAndAllLibs();
        } catch (error) {
            this._handleApiError(error, "清空封面失败");
        } finally {
            this.saving = false;
        }
    },
    
    async saveConfig() {
        this.saving = true;
        try {
            await api.updateConfig(this.config);
            ElMessage.success('系统设置已保存');
            this.originalConfigForComparison = JSON.parse(JSON.stringify(this.config));
        } catch (error) {
            this._handleApiError(error, '保存设置失败');
        } finally {
            this.saving = false;
        }
    },
    openAddDialog() {
        this.isEditing = false;
        this.currentLibrary = { 
            name: '', 
            resource_type: 'collection', 
            resource_id: '',
            merge_by_tmdb_id: false,
            image_tag: null,
            fallback_tmdb_id: null,
            fallback_tmdb_type: null,
            cache_refresh_interval: null,
            source_libraries: []
        };
        this.dialogVisible = true;
    },
    openEditDialog(library) {
        this.isEditing = true;
        this.currentLibrary = JSON.parse(JSON.stringify(library));
        if (this.currentLibrary.merge_by_tmdb_id === undefined) {
            this.currentLibrary.merge_by_tmdb_id = false;
        }
        if (library.resource_type === 'person' && library.resource_id) {
            this.resolvePersonName(library.resource_id);
        }
        this.dialogVisible = true;
    },
    _handleApiError(error, messagePrefix) {
        const detail = error.response?.data?.detail;
        ElMessage.error(`${messagePrefix}: ${detail || '请检查网络或联系管理员'}`);
    },
    async resolvePersonName(personId) {
        if (!personId || this.personNameCache[personId]) { return; }
        this.personNameCache[personId] = '...';
        try {
            const response = await api.resolveItem(personId);
            this.personNameCache[personId] = response.data.name;
        } catch (error) {
            console.error(`解析人员ID ${personId} 失败:`, error);
            this.personNameCache[personId] = '未知';
        }
    },

    // --- Real Libraries ---
    async syncRealLibraries() {
        try {
            const res = await api.syncRealLibraries();
            this.config.real_libraries = res.data;
            ElMessage.success('已从 Emby 同步真实库列表');
        } catch (error) {
            this._handleApiError(error, '同步真实库失败');
        }
    },
    async saveRealLibraries() {
        this.saving = true;
        try {
            await api.saveRealLibraries(this.config.real_libraries, this.config.real_library_cover_cron);
            ElMessage.success('真实库配置已保存');
        } catch (error) {
            this._handleApiError(error, '保存真实库配置失败');
        } finally {
            this.saving = false;
        }
    },
    async refreshRealLibraryCover(id) {
        try {
            const res = await api.refreshRealLibraryCover(id);
            if (res.data.ok) {
                ElMessage.success('封面刷新成功');
                const rl = (this.config.real_libraries || []).find(r => r.id === id);
                if (rl) rl.image_tag = res.data.image_tag;
            } else {
                ElMessage.warning('封面生成失败');
            }
        } catch (error) {
            this._handleApiError(error, '刷新真实库封面失败');
        }
    },
    async refreshAllRealLibraryCovers() {
        try {
            await api.refreshAllRealLibraryCovers();
            ElMessage.success('全部真实库封面刷新已启动，将在后台处理。');
        } catch (error) {
            this._handleApiError(error, '刷新全部封面失败');
        }
    },
  },
});
