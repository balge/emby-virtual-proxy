import { defineStore } from 'pinia'
import { useToast } from '@/composables/useToast'
import api from '../api'

const toast = useToast()

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
    personNameCache: {},
    /** 虚拟库行后台任务进行中（保存后刷新数据 / 点「数据」「封面」等），key 为 library id 字符串 */
    libraryRowSyncing: {},
  }),

  getters: {
    virtualLibraries: (state) => state.config.library || [],

    sortedLibsInDisplayOrder: (state) => {
      if (!state.config.display_order || !state.allLibrariesForSorting.length) return []
      const libMap = new Map(state.allLibrariesForSorting.map(lib => [lib.id, lib]))
      return state.config.display_order.map(id => libMap.get(id)).filter(Boolean)
    },

    unsortedLibs: (state) => {
      if (!state.allLibrariesForSorting.length) return []
      const sortedIds = new Set(state.config.display_order || [])
      return state.allLibrariesForSorting.filter(lib => !sortedIds.has(lib.id))
    },

    availableResources: (state) => {
      const type = state.currentLibrary?.resource_type
      if (!type || !state.classifications) return []
      const pluralTypeMap = { collection: 'collections', tag: 'tags', genre: 'genres', studio: 'studios', person: 'persons' }
      return state.classifications[pluralTypeMap[type]] || []
    },
  },

  actions: {
    async fetchAllInitialData() {
      this.dataLoading = true
      this.dataStatus = null
      try {
        const [configRes, classificationsRes, allLibsRes] = await Promise.all([
          api.getConfig(), api.getClassifications(), api.getAllLibraries(),
        ])
        this.config = configRes.data
        if (!this.config.advanced_filters) this.config.advanced_filters = []
        if (!this.config.library) this.config.library = []
        if (!this.config.real_libraries) this.config.real_libraries = []
        if (this.config.cache_refresh_interval == null) this.config.cache_refresh_interval = 12
        if (!this.config.webhook) this.config.webhook = { enabled: false, secret: null, delay_seconds: 0 }

        this.originalConfigForComparison = JSON.parse(JSON.stringify(configRes.data))
        this.classifications = classificationsRes.data
        this.allLibrariesForSorting = allLibsRes.data

        if (!this.config.display_order?.length && this.allLibrariesForSorting.length) {
          this.config.display_order = this.allLibrariesForSorting.map(l => l.id)
        }
        this.resolveVisiblePersonNames()
        this.dataStatus = { type: 'success', text: 'Emby数据已加载' }
      } catch (error) {
        this._handleApiError(error, '加载初始数据失败')
        this.dataStatus = { type: 'error', text: 'Emby数据加载失败' }
      } finally {
        this.dataLoading = false
      }
    },

    async _reloadConfigAndAllLibs() {
      try {
        const [configRes, allLibsRes] = await Promise.all([api.getConfig(), api.getAllLibraries()])
        this.config = configRes.data
        if (!this.config.advanced_filters) this.config.advanced_filters = []
        if (!this.config.library) this.config.library = []
        this.originalConfigForComparison = JSON.parse(JSON.stringify(configRes.data))
        this.allLibrariesForSorting = allLibsRes.data
        this.resolveVisiblePersonNames()
      } catch (error) {
        this._handleApiError(error, '刷新配置列表失败')
      }
    },

    _sleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms))
    },

    /**
     * 后台 refresh 完成后会把新的 image_tag 写入配置；轮询 /config 直到 tag 变化或超时。
     * @returns {Promise<boolean>} true 表示检测到更新，false 为超时
     */
    async _pollLibraryDataReady(libraryId, previousImageTag, { maxMs = 120000, intervalMs = 2000 } = {}) {
      const idStr = String(libraryId)
      const normalizedPrev = previousImageTag ?? null
      const deadline = Date.now() + maxMs
      while (Date.now() < deadline) {
        await this._sleep(intervalMs)
        try {
          const cfgRes = await api.getConfig()
          const lib = cfgRes.data?.library?.find((l) => String(l.id) === idStr)
          if (!lib) continue
          const tag = lib.image_tag ?? null
          if (tag && tag !== normalizedPrev) return true
        } catch {
          /* 单次轮询失败则继续 */
        }
      }
      return false
    },

    async saveAdvancedFilters(filters) {
      this.saving = true
      try {
        await api.saveAdvancedFilters(filters)
        this.config.advanced_filters = filters
      } catch (error) {
        this._handleApiError(error, '保存高级筛选器失败')
        throw error
      } finally {
        this.saving = false
      }
    },

    async generateLibraryCover(libraryId, titleZh, titleEn, styleName, tempImagePaths) {
      this.coverGenerating = true
      try {
        const response = await api.generateCover(libraryId, titleZh, titleEn, styleName, tempImagePaths)
        if (response.data?.success) {
          toast.success('封面已在后台生成！请点击保存。')
          if (this.currentLibrary?.id === libraryId) {
            this.currentLibrary.image_tag = response.data.image_tag
          }
          return true
        }
        return false
      } catch (error) {
        this._handleApiError(error, '封面生成失败')
        return false
      } finally {
        this.coverGenerating = false
      }
    },

    async saveLibrary() {
      const lib = this.currentLibrary
      if (!lib.name) { toast.warning('请填写所有必填字段'); return }
      if (lib.resource_type === 'rsshub') {
        if (!lib.rsshub_url || !lib.rss_type) { toast.warning('请填写所有必填字段'); return }
      } else if (!['all', 'random'].includes(lib.resource_type)) {
        if (!lib.resource_id) { toast.warning('请填写所有必填字段'); return }
      }

      this.saving = true
      try {
        const res = await (this.isEditing ? api.updateLibrary(lib.id, lib) : api.addLibrary(lib))
        const savedId = this.isEditing ? lib.id : res.data?.id
        if (!savedId) {
          toast.error('保存失败：未返回虚拟库 ID')
          return
        }
        toast.success(this.isEditing ? '虚拟库已更新' : '虚拟库已添加')
        this.dialogVisible = false
        await this._reloadConfigAndAllLibs()

        const row = this.config.library?.find((l) => String(l.id) === String(savedId))
        const prevImageTag = row?.image_tag ?? null
        const idStr = String(savedId)
        this.libraryRowSyncing[idStr] = true
        try {
          await api.refreshLibraryData(savedId)
          const ok = await this._pollLibraryDataReady(savedId, prevImageTag)
          if (!ok) {
            toast.warning('后台仍在生成数据或封面，请稍后查看，或点击「数据」手动刷新')
          }
        } catch (e) {
          this._handleApiError(e, '触发数据与封面刷新失败')
        } finally {
          await this._reloadConfigAndAllLibs()
          delete this.libraryRowSyncing[idStr]
          this.resolveVisiblePersonNames()
        }
      } catch (error) {
        this._handleApiError(error, '保存虚拟库失败')
      } finally {
        this.saving = false
      }
    },

    resolveVisiblePersonNames() {
      if (!this.config.library) return
      for (const lib of this.config.library.filter(l => l.resource_type === 'person')) {
        if (lib.resource_id) this.resolvePersonName(lib.resource_id)
      }
    },

    async fetchAllEmbyData() {
      this.dataLoading = true
      this.dataStatus = { type: 'info', text: '正在刷新...' }
      try {
        const [classificationsRes, allLibsRes] = await Promise.all([api.getClassifications(), api.getAllLibraries()])
        this.classifications = classificationsRes.data
        this.allLibrariesForSorting = allLibsRes.data
        this.dataStatus = { type: 'success', text: 'Emby数据已刷新' }
        toast.success('分类和媒体库数据已从Emby刷新！')
      } catch (error) {
        this._handleApiError(error, '刷新Emby数据失败')
        this.dataStatus = { type: 'error', text: '刷新失败' }
      } finally {
        this.dataLoading = false
      }
    },

    async saveDisplayOrder(orderedIds) {
      this.saving = true
      try {
        this.config.display_order = orderedIds
        await api.saveDisplayOrder(this.config.display_order)
        toast.success('主页布局已保存！')
        await this._reloadConfigAndAllLibs()
      } catch (error) {
        this._handleApiError(error, '保存布局失败')
      } finally {
        this.saving = false
      }
    },

    openLayoutManager() { this.layoutManagerVisible = true },

    async deleteLibrary(id) {
      try {
        await api.deleteLibrary(id)
        toast.success('虚拟库已删除')
        await this._reloadConfigAndAllLibs()
      } catch (error) {
        this._handleApiError(error, '删除虚拟库失败')
      }
    },

    async toggleLibraryHidden(id) {
      try {
        const res = await api.toggleLibraryHidden(id)
        toast.success(res.data.hidden ? '虚拟库已隐藏' : '虚拟库已恢复显示')
        await this._reloadConfigAndAllLibs()
      } catch (error) {
        this._handleApiError(error, '切换隐藏状态失败')
      }
    },

    async refreshRssLibrary(id) {
      try {
        await api.refreshRssLibrary(id)
        toast.info('RSS 刷新已提交。拉取与入库较慢，可能要等几分钟，请稍后在客户端或刷新本页查看。')
        await this._reloadConfigAndAllLibs()
      } catch (e) {
        this._handleApiError(e, '刷新 RSS 库失败')
      }
    },
    async refreshLibraryCover(id) {
      const idStr = String(id)
      const lib = this.config.library?.find((l) => String(l.id) === idStr)
      const prevImageTag = lib?.image_tag ?? null
      this.libraryRowSyncing[idStr] = true
      try {
        await api.refreshLibraryCover(id)
        const ok = await this._pollLibraryDataReady(idStr, prevImageTag)
        if (ok) toast.success('封面已更新')
        else toast.warning('封面仍在生成，请稍后查看')
      } catch (e) {
        this._handleApiError(e, '刷新封面失败')
      } finally {
        await this._reloadConfigAndAllLibs()
        delete this.libraryRowSyncing[idStr]
        this.resolveVisiblePersonNames()
      }
    },
    async refreshLibraryData(id) {
      const idStr = String(id)
      const lib = this.config.library?.find((l) => String(l.id) === idStr)
      const prevImageTag = lib?.image_tag ?? null
      this.libraryRowSyncing[idStr] = true
      try {
        await api.refreshLibraryData(id)
        const ok = await this._pollLibraryDataReady(idStr, prevImageTag)
        if (ok) toast.success('数据与封面已更新')
        else toast.warning('后台仍在处理，请稍后查看或再次点击「数据」')
      } catch (e) {
        this._handleApiError(e, '刷新数据失败')
      } finally {
        await this._reloadConfigAndAllLibs()
        delete this.libraryRowSyncing[idStr]
        this.resolveVisiblePersonNames()
      }
    },
    async refreshAllCovers() {
      try { await api.refreshAllCovers(); toast.success('所有虚拟库封面刷新已启动') } catch (e) { this._handleApiError(e, '刷新所有封面失败') }
    },
    async restartProxyServer() {
      this.saving = true
      try { await api.restartProxy(); toast.success('代理服务重启命令已发送！') } catch (e) { this._handleApiError(e, '重启代理服务失败') } finally { this.saving = false }
    },
    async clearAllCovers() {
      this.saving = true
      try { await api.clearCovers(); toast.success('所有本地封面已清空！'); await this._reloadConfigAndAllLibs() } catch (e) { this._handleApiError(e, '清空封面失败') } finally { this.saving = false }
    },
    async saveConfig() {
      this.saving = true
      try {
        await api.updateConfig(this.config)
        toast.success('系统设置已保存')
        this.originalConfigForComparison = JSON.parse(JSON.stringify(this.config))
      } catch (e) { this._handleApiError(e, '保存设置失败') } finally { this.saving = false }
    },

    openAddDialog() {
      this.isEditing = false
      this.currentLibrary = {
        name: '', resource_type: 'collection', resource_id: '',
        merge_by_tmdb_id: false, image_tag: null, fallback_tmdb_id: null,
        fallback_tmdb_type: null, cache_refresh_interval: null, source_libraries: [],
      }
      this.dialogVisible = true
    },
    openEditDialog(library) {
      this.isEditing = true
      this.currentLibrary = JSON.parse(JSON.stringify(library))
      if (this.currentLibrary.merge_by_tmdb_id === undefined) this.currentLibrary.merge_by_tmdb_id = false
      if (library.resource_type === 'person' && library.resource_id) this.resolvePersonName(library.resource_id)
      this.dialogVisible = true
    },

    _handleApiError(error, messagePrefix) {
      const detail = error.response?.data?.detail
      toast.error(`${messagePrefix}: ${detail || '请检查网络或联系管理员'}`)
    },

    async resolvePersonName(personId) {
      if (!personId || this.personNameCache[personId]) return
      this.personNameCache[personId] = '...'
      try {
        const response = await api.resolveItem(personId)
        this.personNameCache[personId] = response.data.name
      } catch { this.personNameCache[personId] = '未知' }
    },

    // Real Libraries
    async syncRealLibraries() {
      try { const res = await api.syncRealLibraries(); this.config.real_libraries = res.data; toast.success('已从 Emby 同步真实库列表') } catch (e) { this._handleApiError(e, '同步真实库失败') }
    },
    async saveRealLibraries() {
      this.saving = true
      try { await api.saveRealLibraries(this.config.real_libraries, this.config.real_library_cover_cron); toast.success('真实库配置已保存') } catch (e) { this._handleApiError(e, '保存真实库配置失败') } finally { this.saving = false }
    },
    async refreshRealLibraryCover(id) {
      try {
        const res = await api.refreshRealLibraryCover(id)
        if (res.data.ok) { toast.success('封面刷新成功'); const rl = this.config.real_libraries?.find(r => r.id === id); if (rl) rl.image_tag = res.data.image_tag }
        else toast.warning('封面生成失败')
      } catch (e) { this._handleApiError(e, '刷新真实库封面失败') }
    },
    async refreshAllRealLibraryCovers() {
      try { await api.refreshAllRealLibraryCovers(); toast.success('全部真实库封面刷新已启动') } catch (e) { this._handleApiError(e, '刷新全部封面失败') }
    },
  },
})
