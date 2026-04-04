<template>
  <div>
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6">
      <div>
        <h1 class="text-2xl font-bold text-gray-900 dark:text-gray-100">虚拟媒体库</h1>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">创建、编辑和管理虚拟库</p>
      </div>
      <div class="grid grid-cols-2 gap-2 w-full sm:w-auto sm:flex sm:flex-wrap sm:justify-end">
        <BaseButton class="min-w-0" @click="store.refreshAllCovers()">刷新所有封面</BaseButton>
        <BaseButton class="min-w-0" @click="store.fetchAllEmbyData" :loading="store.dataLoading">刷新Emby数据</BaseButton>
        <BaseButton class="min-w-0" @click="store.openLayoutManager">调整布局</BaseButton>
        <BaseButton class="min-w-0" variant="primary" @click="store.openAddDialog">添加虚拟库</BaseButton>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="store.dataLoading" class="flex items-center justify-center py-20">
      <svg class="animate-spin h-6 w-6 text-primary-500" viewBox="0 0 24 24" fill="none">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
    </div>

    <!-- Empty -->
    <div v-else-if="!store.virtualLibraries.length" class="text-center py-20">
      <RectangleStackIcon class="w-12 h-12 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
      <p class="text-gray-500 dark:text-gray-400">暂无虚拟库</p>
      <BaseButton variant="primary" class="mt-4" @click="store.openAddDialog">创建第一个虚拟库</BaseButton>
    </div>

    <!-- Desktop table -->
    <div v-else class="hidden md:block bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
            <th class="text-left px-4 py-3 font-medium text-gray-500 dark:text-gray-400">名称</th>
            <th class="text-center px-4 py-3 font-medium text-gray-500 dark:text-gray-400 w-44">封面</th>
            <th class="text-left px-4 py-3 font-medium text-gray-500 dark:text-gray-400 w-20">类型</th>
            <th class="text-left px-4 py-3 font-medium text-gray-500 dark:text-gray-400">详情</th>
            <th class="text-right px-4 py-3 font-medium text-gray-500 dark:text-gray-400">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100 dark:divide-gray-700/50">
          <tr
            v-for="row in store.virtualLibraries"
            :key="row.id"
            class="transition-colors"
            :class="[
              row.hidden && !rowSyncing(row.id) ? 'opacity-50' : '',
              rowSyncing(row.id)
                ? 'relative bg-primary-50/40 dark:bg-primary-950/25 pointer-events-none'
                : 'hover:bg-gray-50 dark:hover:bg-gray-700/30',
            ]"
          >
            <td class="px-4 py-3">
              <div class="flex items-center gap-2 min-w-0">
                <template v-if="rowSyncing(row.id)">
                  <span class="inline-flex shrink-0 text-primary-600 dark:text-primary-400" aria-hidden="true">
                    <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  </span>
                  <span class="text-xs font-medium text-primary-700 dark:text-primary-300 shrink-0">生成中</span>
                </template>
                <span class="font-medium text-gray-900 dark:text-gray-100 truncate">{{ row.name }}</span>
                <BaseTag v-if="row.hidden" variant="default">已隐藏</BaseTag>
              </div>
            </td>
            <td class="px-4 py-3 text-center">
              <div v-if="row.image_tag" class="w-36 mx-auto aspect-video rounded-lg overflow-hidden bg-gray-100 dark:bg-gray-700">
                <img :src="`/covers/${row.id}.jpg?t=${row.image_tag}`" class="w-full h-full object-cover" />
              </div>
              <span v-else class="text-gray-300 dark:text-gray-600">—</span>
            </td>
            <td class="px-4 py-3">
              <BaseTag :variant="typeTagVariant(row.resource_type)">{{ getTypeLabel(row.resource_type) }}</BaseTag>
            </td>
            <td class="px-4 py-3">
              <div class="space-y-1">
                <p class="text-xs text-gray-600 dark:text-gray-400 truncate max-w-xs" :title="getResourceDetail(row)">
                  {{ getResourceDetail(row) }}
                </p>
                <div class="flex flex-wrap gap-1">
                  <BaseTag v-if="row.merge_by_tmdb_id" variant="success">TMDB合并</BaseTag>
                  <BaseTooltip v-if="row.source_libraries?.length" :text="getSourceLibNames(row)">
                    <BaseTag variant="warning">源库: {{ getSourceLibSummary(row) }}</BaseTag>
                  </BaseTooltip>
                  <BaseTag v-else-if="row.resource_type !== 'rsshub'" variant="default">全部源库</BaseTag>
                  <BaseTag v-if="row.advanced_filter_id" variant="info">{{ getFilterName(row.advanced_filter_id) }}</BaseTag>
                  <BaseTag v-if="getRefreshText(row)" variant="default">{{ getRefreshText(row) }}</BaseTag>
                </div>
                <p v-if="row.resource_type === 'rsshub' && row.rsshub_url" class="text-[11px] text-gray-400 dark:text-gray-500 break-all font-mono leading-relaxed">
                  {{ row.rsshub_url }}
                </p>
              </div>
            </td>
            <td class="px-4 py-3">
              <div class="flex justify-end gap-1 flex-wrap">
                <BaseTooltip v-if="row.resource_type === 'rsshub'" text="刷新 RSS">
                  <BaseButton size="xs" variant="success-outline" @click="store.refreshRssLibrary(row.id)"><RssIcon class="w-3.5 h-3.5" /> RSS</BaseButton>
                </BaseTooltip>
                <BaseTooltip text="刷新封面">
                  <BaseButton size="xs" @click="store.refreshLibraryCover(row.id)"><ArrowPathIcon class="w-3.5 h-3.5" /> 封面</BaseButton>
                </BaseTooltip>
                <BaseTooltip v-if="row.resource_type !== 'rsshub'" text="刷新数据和封面">
                  <BaseButton size="xs" variant="success-outline" @click="store.refreshLibraryData(row.id)"><CircleStackIcon class="w-3.5 h-3.5" /> 数据</BaseButton>
                </BaseTooltip>
                <BaseTooltip text="编辑虚拟库">
                  <BaseButton size="xs" @click="store.openEditDialog(row)"><PencilSquareIcon class="w-3.5 h-3.5" /> 编辑</BaseButton>
                </BaseTooltip>
                <BaseTooltip :text="row.hidden ? '显示虚拟库' : '隐藏虚拟库'">
                  <BaseButton size="xs" :variant="row.hidden ? 'warning' : 'ghost'" @click="store.toggleLibraryHidden(row.id)">
                    <EyeIcon v-if="row.hidden" class="w-3.5 h-3.5" /><EyeSlashIcon v-else class="w-3.5 h-3.5" /> {{ row.hidden ? '显示' : '隐藏' }}
                  </BaseButton>
                </BaseTooltip>
                <ConfirmPopover :message="`确定删除虚拟库「${row.name}」？`" confirm-text="删除" @confirm="store.deleteLibrary(row.id)">
                  <template #trigger>
                    <BaseTooltip text="删除虚拟库">
                      <BaseButton size="xs" variant="danger-outline"><TrashIcon class="w-3.5 h-3.5" /> 删除</BaseButton>
                    </BaseTooltip>
                  </template>
                </ConfirmPopover>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Mobile cards -->
    <div class="md:hidden space-y-3">
      <div
        v-for="row in store.virtualLibraries"
        :key="row.id"
        class="relative bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden"
        :class="[
          row.hidden && !rowSyncing(row.id) ? 'opacity-50' : '',
          rowSyncing(row.id) ? 'ring-2 ring-primary-500/40 ring-inset' : '',
        ]"
      >
        <div
          v-if="rowSyncing(row.id)"
          class="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2 bg-white/85 dark:bg-gray-900/85 backdrop-blur-[2px]"
        >
          <svg class="animate-spin h-8 w-8 text-primary-600 dark:text-primary-400" viewBox="0 0 24 24" fill="none">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <span class="text-xs font-medium text-primary-800 dark:text-primary-200">正在生成数据与封面…</span>
        </div>
        <!-- 16:9 cover banner -->
        <div v-if="row.image_tag" class="w-full aspect-video bg-gray-100 dark:bg-gray-700">
          <img :src="`/covers/${row.id}.jpg?t=${row.image_tag}`" class="w-full h-full object-cover" />
        </div>
        <!-- Content -->
        <div class="p-4">
          <div class="flex items-center gap-1.5 mb-1.5">
            <span class="font-semibold text-gray-900 dark:text-gray-100 text-sm truncate">{{ row.name }}</span>
            <BaseTag v-if="row.hidden" variant="default">隐藏</BaseTag>
            <BaseTag :variant="typeTagVariant(row.resource_type)">{{ getTypeLabel(row.resource_type) }}</BaseTag>
          </div>
          <p class="text-xs text-gray-500 dark:text-gray-400 truncate mb-1.5" :title="getResourceDetail(row)">{{ getResourceDetail(row) }}</p>
          <div class="flex flex-wrap gap-1 mb-2">
            <BaseTag v-if="row.merge_by_tmdb_id" variant="success">TMDB合并</BaseTag>
            <BaseTooltip v-if="row.source_libraries?.length" :text="getSourceLibNames(row)" position="bottom">
              <BaseTag variant="warning">源库: {{ getSourceLibSummary(row) }}</BaseTag>
            </BaseTooltip>
            <BaseTag v-if="row.advanced_filter_id" variant="info">{{ getFilterName(row.advanced_filter_id) }}</BaseTag>
            <BaseTag v-if="getRefreshText(row)" variant="default">{{ getRefreshText(row) }}</BaseTag>
          </div>
          <p v-if="row.resource_type === 'rsshub' && row.rsshub_url" class="text-[11px] text-gray-400 dark:text-gray-500 break-all mb-2.5 font-mono leading-relaxed">
            {{ row.rsshub_url }}
          </p>
          <!-- Action buttons -->
          <div class="flex flex-wrap gap-1.5">
            <BaseButton v-if="row.resource_type === 'rsshub'" size="xs" variant="success-outline" title="刷新 RSS" @click="store.refreshRssLibrary(row.id)"><RssIcon class="w-3.5 h-3.5" /> RSS</BaseButton>
            <BaseButton size="xs" title="刷新封面" @click="store.refreshLibraryCover(row.id)"><ArrowPathIcon class="w-3.5 h-3.5" /> 封面</BaseButton>
            <BaseButton v-if="row.resource_type !== 'rsshub'" size="xs" variant="success-outline" title="刷新数据和封面" @click="store.refreshLibraryData(row.id)"><CircleStackIcon class="w-3.5 h-3.5" /> 数据</BaseButton>
            <BaseButton size="xs" title="编辑虚拟库" @click="store.openEditDialog(row)"><PencilSquareIcon class="w-3.5 h-3.5" /> 编辑</BaseButton>
            <BaseButton size="xs" :variant="row.hidden ? 'warning' : 'ghost'" :title="row.hidden ? '显示虚拟库' : '隐藏虚拟库'" @click="store.toggleLibraryHidden(row.id)">
              <EyeIcon v-if="row.hidden" class="w-3.5 h-3.5" /><EyeSlashIcon v-else class="w-3.5 h-3.5" /> {{ row.hidden ? '显示' : '隐藏' }}
            </BaseButton>
            <ConfirmPopover :message="`确定删除「${row.name}」？`" confirm-text="删除" @confirm="store.deleteLibrary(row.id)">
              <template #trigger><BaseButton size="xs" variant="danger-outline" title="删除虚拟库"><TrashIcon class="w-3.5 h-3.5" /> 删除</BaseButton></template>
            </ConfirmPopover>
          </div>
        </div>
      </div>
    </div>

    <LibraryEditDialog />
    <DisplayOrderManager />
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import {
  RectangleStackIcon, ArrowPathIcon, CircleStackIcon,
  PencilSquareIcon, EyeIcon, EyeSlashIcon, TrashIcon, RssIcon,
} from '@heroicons/vue/24/outline'
import { useMainStore } from '@/stores/main'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseTag from '@/components/ui/BaseTag.vue'
import BaseTooltip from '@/components/ui/BaseTooltip.vue'
import ConfirmPopover from '@/components/ui/ConfirmPopover.vue'
import LibraryEditDialog from '@/components/LibraryEditDialog.vue'
import DisplayOrderManager from '@/components/DisplayOrderManager.vue'

const store = useMainStore()

const rowSyncing = (id) => !!store.libraryRowSyncing[String(id)]

const typeMap = { collection: '合集', tag: '标签', genre: '类型', studio: '工作室', person: '人员', rsshub: 'RSS', random: '推荐', all: '全库' }
const getTypeLabel = (t) => typeMap[t] || '未知'
const typeTagVariant = (t) => ({ rsshub: 'warning', random: 'info', all: 'primary' }[t] || 'default')

const getRealLibName = (id) => {
  const lib = store.allLibrariesForSorting.find(l => l.id === id)
  return lib?.name || id?.slice(0, 8) || '?'
}

const getSourceLibSummary = (row) => {
  const libs = row.source_libraries || []
  if (libs.length <= 2) return libs.map(getRealLibName).join(', ')
  return `${getRealLibName(libs[0])}, ${getRealLibName(libs[1])} 等${libs.length}个`
}

const getSourceLibNames = (row) => (row.source_libraries || []).map(getRealLibName).join(', ')

const getFilterName = (filterId) => {
  const f = (store.config.advanced_filters || []).find(f => f.id === filterId)
  return f ? `筛选: ${f.name}` : '筛选器'
}

const getRefreshText = (row) => {
  const h = Number(row.cache_refresh_interval)
  return Number.isFinite(h) && h > 0 ? `${h}h刷新` : ''
}

const getResourceDetail = (row) => {
  const type = row.resource_type
  if (type === 'rsshub') {
    const rssType = row.rss_type === 'douban' ? '豆瓣' : row.rss_type === 'bangumi' ? 'Bangumi' : row.rss_type || ''
    const parts = [rssType]
    if (row.enable_retention) parts.push(`保留${row.retention_days || 0}天`)
    if (row.fallback_tmdb_id) parts.push(`追加TMDB:${row.fallback_tmdb_id}`)
    return parts.join(' · ')
  }
  if (type === 'random') return '基于播放记录的偏好推荐'
  if (type === 'all') return '全部媒体库'
  if (type === 'person') {
    const name = store.personNameCache[row.resource_id]
    return name && name !== '...' ? name : `ID: ${row.resource_id || '—'}`
  }
  const keyMap = { collection: 'collections', tag: 'tags', genre: 'genres', studio: 'studios' }
  const list = store.classifications[keyMap[type]] || []
  const found = list.find(r => r.id === row.resource_id)
  return found ? found.name : `ID: ${row.resource_id || '—'}`
}

onMounted(() => { if (!store.dataStatus) store.fetchAllInitialData() })
</script>
