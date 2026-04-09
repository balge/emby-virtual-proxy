<template>
  <div>
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6">
      <div>
        <h1 class="text-2xl font-bold text-gray-900 dark:text-gray-100">媒体库管理</h1>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">同步、配置真实媒体库与封面</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <BaseButton @click="store.syncRealLibraries()" :loading="store.dataLoading">从 Emby 同步</BaseButton>
        <BaseButton @click="store.refreshAllRealLibraryCovers()">刷新全部封面</BaseButton>
        <BaseButton variant="primary" @click="store.saveRealLibraries()" :loading="store.saving">保存配置</BaseButton>
      </div>
    </div>

    <!-- Cron -->
    <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4 mb-6">
      <BaseInput
        v-model="store.config.real_library_cover_cron"
        label="封面定时刷新（Cron 表达式）"
        placeholder="例如: 0 3 * * *（每天凌晨3点）"
        hint="留空则不自动刷新。格式：分 时 日 月 周。保存后生效。"
      />
    </div>

    <!-- Empty -->
    <div v-if="!libs.length" class="text-center py-20">
      <BuildingLibraryIcon class="w-12 h-12 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
      <p class="text-gray-500 dark:text-gray-400">暂无真实库数据</p>
      <BaseButton class="mt-4" @click="store.syncRealLibraries()">从 Emby 同步</BaseButton>
    </div>

    <!-- Desktop table -->
    <div v-else class="hidden md:block bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
            <th class="text-center px-3 py-3 font-medium text-gray-500 dark:text-gray-400 w-16">启用</th>
            <th class="text-center px-3 py-3 font-medium text-gray-500 dark:text-gray-400 w-36">封面</th>
            <th class="text-left px-3 py-3 font-medium text-gray-500 dark:text-gray-400">库名称</th>
            <th class="text-center px-3 py-3 font-medium text-gray-500 dark:text-gray-400 w-20">生成封面</th>
            <th class="text-left px-3 py-3 font-medium text-gray-500 dark:text-gray-400">中文标题</th>
            <th class="text-left px-3 py-3 font-medium text-gray-500 dark:text-gray-400">英文标题</th>
            <th class="text-center px-3 py-3 font-medium text-gray-500 dark:text-gray-400 w-24">操作</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100 dark:divide-gray-700/50">
          <tr v-for="row in libs" :key="row.id" class="hover:bg-gray-50 dark:hover:bg-gray-700/30 transition-colors">
            <td class="px-3 py-3 text-center"><BaseSwitch v-model="row.enabled" /></td>
            <td class="px-3 py-3 text-center">
              <img :src="getCoverUrl(row)" class="h-14 rounded-md mx-auto object-cover" @error="$event.target.style.display='none'" />
            </td>
            <td class="px-3 py-3 font-medium text-gray-900 dark:text-gray-100">{{ row.name }}</td>
            <td class="px-3 py-3 text-center"><BaseSwitch v-model="row.cover_enabled" /></td>
            <td class="px-3 py-3">
              <input v-model="row.cover_title_zh" :placeholder="row.name" :disabled="!row.cover_enabled"
                class="w-full rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 focus:outline-none disabled:opacity-50" />
            </td>
            <td class="px-3 py-3">
              <input v-model="row.cover_title_en" placeholder="可选" :disabled="!row.cover_enabled"
                class="w-full rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 focus:outline-none disabled:opacity-50" />
            </td>
            <td class="px-3 py-3 text-center">
              <BaseButton size="xs" :disabled="!row.cover_enabled" @click="store.refreshRealLibraryCover(row.id)"><ArrowPathIcon class="w-3.5 h-3.5" /> 封面</BaseButton>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Mobile cards -->
    <div class="md:hidden space-y-3">
      <div v-for="row in libs" :key="row.id" class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4">
        <div class="flex gap-3 mb-3">
          <img :src="getCoverUrl(row)" class="w-24 h-auto rounded-lg object-cover shrink-0" @error="$event.target.style.display='none'" />
          <div class="flex-1 min-w-0">
            <p class="font-medium text-gray-900 dark:text-gray-100 text-sm mb-2">{{ row.name }}</p>
            <div class="flex items-center gap-3 text-xs">
              <label class="flex items-center gap-1.5"><BaseSwitch v-model="row.enabled" /> <span class="text-gray-500">启用</span></label>
              <label class="flex items-center gap-1.5"><BaseSwitch v-model="row.cover_enabled" /> <span class="text-gray-500">封面</span></label>
            </div>
          </div>
        </div>
        <div v-if="row.cover_enabled" class="space-y-2 mb-3">
          <input v-model="row.cover_title_zh" :placeholder="row.name"
            class="w-full rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:outline-none" />
          <input v-model="row.cover_title_en" placeholder="英文标题（可选）"
            class="w-full rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:outline-none" />
        </div>
        <BaseButton size="sm" :disabled="!row.cover_enabled" @click="store.refreshRealLibraryCover(row.id)"><ArrowPathIcon class="w-3.5 h-3.5" /> 封面</BaseButton>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { BuildingLibraryIcon, ArrowPathIcon } from '@heroicons/vue/24/outline'
import { useMainStore } from '@/stores/main'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSwitch from '@/components/ui/BaseSwitch.vue'
import BaseInput from '@/components/ui/BaseInput.vue'

const store = useMainStore()
const libs = computed(() => store.config.real_libraries || [])

const getCoverUrl = (row) => {
  if (row.image_tag) return `/covers/${row.id}.jpg?t=${row.image_tag}`
  return `/api/emby/image-proxy/${row.id}`
}

onMounted(() => { if (!store.dataStatus) store.fetchAllInitialData() })
</script>
