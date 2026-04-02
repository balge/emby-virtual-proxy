<template>
  <div>
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6">
      <div>
        <h1 class="text-2xl font-bold text-gray-900 dark:text-gray-100">虚拟媒体库</h1>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">创建、编辑和管理虚拟库</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <BaseButton @click="store.refreshAllCovers()">刷新所有封面</BaseButton>
        <BaseButton @click="store.fetchAllEmbyData" :loading="store.dataLoading">刷新Emby数据</BaseButton>
        <BaseButton @click="store.openLayoutManager">调整布局</BaseButton>
        <BaseButton variant="primary" @click="store.openAddDialog">添加虚拟库</BaseButton>
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
            <th class="text-center px-4 py-3 font-medium text-gray-500 dark:text-gray-400 w-40">封面</th>
            <th class="text-left px-4 py-3 font-medium text-gray-500 dark:text-gray-400">类型</th>
            <th class="text-center px-4 py-3 font-medium text-gray-500 dark:text-gray-400 w-20">合并</th>
            <th class="text-right px-4 py-3 font-medium text-gray-500 dark:text-gray-400">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100 dark:divide-gray-700/50">
          <tr v-for="row in store.virtualLibraries" :key="row.id" class="hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors" :class="{ 'opacity-50': row.hidden }">
            <td class="px-4 py-3">
              <div class="flex items-center gap-2">
                <span class="font-medium text-gray-900 dark:text-gray-100">{{ row.name }}</span>
                <BaseTag v-if="row.hidden" variant="default">已隐藏</BaseTag>
              </div>
            </td>
            <td class="px-4 py-3 text-center">
              <img v-if="row.image_tag" :src="`/covers/${row.id}.jpg?t=${row.image_tag}`" class="h-14 rounded-md mx-auto object-cover" />
              <span v-else class="text-gray-300 dark:text-gray-600">—</span>
            </td>
            <td class="px-4 py-3">
              <BaseTag :variant="typeTagVariant(row.resource_type)">{{ getTypeLabel(row.resource_type) }}</BaseTag>
            </td>
            <td class="px-4 py-3 text-center">
              <BaseTag :variant="row.merge_by_tmdb_id ? 'success' : 'default'">{{ row.merge_by_tmdb_id ? '是' : '否' }}</BaseTag>
            </td>
            <td class="px-4 py-3">
              <div class="flex justify-end gap-1 flex-wrap">
                <BaseButton v-if="row.resource_type === 'rsshub'" size="xs" variant="success-outline" @click="store.refreshRssLibrary(row.id)">RSS</BaseButton>
                <BaseButton size="xs" @click="store.refreshLibraryCover(row.id)">封面</BaseButton>
                <BaseButton v-if="row.resource_type !== 'rsshub'" size="xs" variant="success-outline" @click="store.refreshLibraryData(row.id)">数据</BaseButton>
                <BaseButton size="xs" @click="store.openEditDialog(row)">编辑</BaseButton>
                <BaseButton size="xs" :variant="row.hidden ? 'warning' : 'ghost'" @click="store.toggleLibraryHidden(row.id)">{{ row.hidden ? '显示' : '隐藏' }}</BaseButton>
                <ConfirmPopover :message="`确定删除虚拟库「${row.name}」？`" confirm-text="删除" @confirm="store.deleteLibrary(row.id)">
                  <template #trigger><BaseButton size="xs" variant="danger-outline">删除</BaseButton></template>
                </ConfirmPopover>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Mobile cards -->
    <div class="md:hidden space-y-3">
      <div v-for="row in store.virtualLibraries" :key="row.id"
        class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4"
        :class="{ 'opacity-50': row.hidden }">
        <div class="flex gap-3 mb-3">
          <img v-if="row.image_tag" :src="`/covers/${row.id}.jpg?t=${row.image_tag}`" class="w-24 h-auto rounded-lg object-cover shrink-0" />
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-1.5 mb-1">
              <span class="font-medium text-gray-900 dark:text-gray-100 text-sm truncate">{{ row.name }}</span>
              <BaseTag v-if="row.hidden" variant="default">隐藏</BaseTag>
            </div>
            <BaseTag :variant="typeTagVariant(row.resource_type)" class="mb-1">{{ getTypeLabel(row.resource_type) }}</BaseTag>
            <div class="flex gap-1 mt-1">
              <BaseTag v-if="row.merge_by_tmdb_id" variant="success">TMDB合并</BaseTag>
              <BaseTag v-if="row.source_libraries?.length" variant="warning">{{ row.source_libraries.length }}个源库</BaseTag>
            </div>
          </div>
        </div>
        <div class="flex flex-wrap gap-1.5">
          <BaseButton v-if="row.resource_type === 'rsshub'" size="xs" variant="success-outline" @click="store.refreshRssLibrary(row.id)">RSS</BaseButton>
          <BaseButton size="xs" @click="store.refreshLibraryCover(row.id)">封面</BaseButton>
          <BaseButton v-if="row.resource_type !== 'rsshub'" size="xs" variant="success-outline" @click="store.refreshLibraryData(row.id)">数据</BaseButton>
          <BaseButton size="xs" @click="store.openEditDialog(row)">编辑</BaseButton>
          <BaseButton size="xs" :variant="row.hidden ? 'warning' : 'ghost'" @click="store.toggleLibraryHidden(row.id)">{{ row.hidden ? '显示' : '隐藏' }}</BaseButton>
          <ConfirmPopover :message="`确定删除「${row.name}」？`" confirm-text="删除" @confirm="store.deleteLibrary(row.id)">
            <template #trigger><BaseButton size="xs" variant="danger-outline">删除</BaseButton></template>
          </ConfirmPopover>
        </div>
      </div>
    </div>

    <LibraryEditDialog />
    <DisplayOrderManager />
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { RectangleStackIcon } from '@heroicons/vue/24/outline'
import { useMainStore } from '@/stores/main'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseTag from '@/components/ui/BaseTag.vue'
import ConfirmPopover from '@/components/ui/ConfirmPopover.vue'
import LibraryEditDialog from '@/components/LibraryEditDialog.vue'
import DisplayOrderManager from '@/components/DisplayOrderManager.vue'

const store = useMainStore()

const typeMap = { collection: '合集', tag: '标签', genre: '类型', studio: '工作室', person: '人员', rsshub: 'RSS', random: '推荐', all: '全库' }
const getTypeLabel = (t) => typeMap[t] || '未知'
const typeTagVariant = (t) => ({ rsshub: 'warning', random: 'info', all: 'primary' }[t] || 'default')

onMounted(() => { if (!store.dataStatus) store.fetchAllInitialData() })
</script>
