<template>
  <div>
    <h1 class="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-1">欢迎回来</h1>
    <p class="text-sm text-gray-500 dark:text-gray-400 mb-8">Emby Virtual Proxy 配置面板</p>

    <!-- Status -->
    <div v-if="store.dataLoading" class="flex items-center gap-2 text-sm text-gray-500 mb-6">
      <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      正在加载数据...
    </div>

    <!-- Quick stats -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
      <div class="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm border border-gray-200 dark:border-gray-700">
        <p class="text-2xl font-bold text-primary-600">{{ store.virtualLibraries.length }}</p>
        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">虚拟媒体库</p>
      </div>
      <div class="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm border border-gray-200 dark:border-gray-700">
        <p class="text-2xl font-bold text-emerald-600">{{ (store.config.real_libraries || []).length }}</p>
        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">真实媒体库</p>
      </div>
      <div class="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm border border-gray-200 dark:border-gray-700">
        <p class="text-2xl font-bold text-amber-600">{{ (store.config.advanced_filters || []).length }}</p>
        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">高级筛选器</p>
      </div>
    </div>

    <!-- Quick links -->
    <h2 class="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">快捷操作</h2>
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <router-link
        v-for="link in quickLinks"
        :key="link.to"
        :to="link.to"
        class="flex items-center gap-3 p-4 bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 hover:border-primary-300 dark:hover:border-primary-700 transition-colors group"
      >
        <div class="w-10 h-10 rounded-lg flex items-center justify-center shrink-0" :class="link.iconBg">
          <component :is="link.icon" class="w-5 h-5" :class="link.iconColor" />
        </div>
        <div>
          <p class="text-sm font-medium text-gray-900 dark:text-gray-100 group-hover:text-primary-600 dark:group-hover:text-primary-400 transition-colors">{{ link.label }}</p>
          <p class="text-xs text-gray-500 dark:text-gray-400">{{ link.desc }}</p>
        </div>
      </router-link>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useMainStore } from '@/stores/main'
import {
  Cog6ToothIcon, RectangleStackIcon, FunnelIcon, BuildingLibraryIcon,
} from '@heroicons/vue/24/outline'

const store = useMainStore()

const quickLinks = [
  { to: '/settings', label: '核心设置', desc: 'Emby 连接、缓存、Webhook', icon: Cog6ToothIcon, iconBg: 'bg-blue-100 dark:bg-blue-900/30', iconColor: 'text-blue-600 dark:text-blue-400' },
  { to: '/virtual', label: '虚拟媒体库', desc: '创建和管理虚拟库', icon: RectangleStackIcon, iconBg: 'bg-emerald-100 dark:bg-emerald-900/30', iconColor: 'text-emerald-600 dark:text-emerald-400' },
  { to: '/filters', label: '高级筛选器', desc: '配置筛选规则', icon: FunnelIcon, iconBg: 'bg-amber-100 dark:bg-amber-900/30', iconColor: 'text-amber-600 dark:text-amber-400' },
  { to: '/libraries', label: '媒体库管理', desc: '真实库同步与封面', icon: BuildingLibraryIcon, iconBg: 'bg-purple-100 dark:bg-purple-900/30', iconColor: 'text-purple-600 dark:text-purple-400' },
]

onMounted(() => {
  if (!store.dataStatus) store.fetchAllInitialData()
})
</script>
