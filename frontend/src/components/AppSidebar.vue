<template>
  <aside class="hidden lg:flex lg:flex-col lg:w-60 lg:fixed lg:inset-y-0 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 z-30">
    <!-- Logo -->
    <div class="flex items-center gap-2 px-5 h-16 border-b border-gray-200 dark:border-gray-800 shrink-0">
      <img src="/favicon.png" alt="Logo" class="w-8 h-8 rounded-lg" />
      <span class="font-semibold text-gray-900 dark:text-gray-100 text-sm">Emby Proxy</span>
    </div>

    <!-- Nav -->
    <nav class="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
      <router-link
        v-for="item in navItems"
        :key="item.to"
        :to="item.to"
        class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors"
        :class="isActive(item.to)
          ? 'bg-primary-50 text-primary-700 dark:bg-primary-900/30 dark:text-primary-300'
          : 'text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800'"
      >
        <component :is="item.icon" class="w-5 h-5 shrink-0" />
        {{ item.label }}
      </router-link>
    </nav>

    <!-- Footer -->
    <div class="px-3 py-3 border-t border-gray-200 dark:border-gray-800 space-y-2">
      <button
        @click="toggleDark"
        class="flex items-center gap-3 w-full px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800 transition-colors"
      >
        <MoonIcon v-if="!isDark" class="w-5 h-5" />
        <SunIcon v-else class="w-5 h-5" />
        {{ isDark ? '浅色模式' : '深色模式' }}
      </button>
      <button
        v-if="authEnabled"
        @click="$emit('logout')"
        class="flex items-center gap-3 w-full px-3 py-2 rounded-lg text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800 transition-colors"
      >
        <ArrowRightStartOnRectangleIcon class="w-5 h-5" />
        退出登录
      </button>
    </div>
  </aside>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import {
  HomeIcon, Cog6ToothIcon, FunnelIcon, BuildingLibraryIcon, RectangleStackIcon,
  MoonIcon, SunIcon, ArrowRightStartOnRectangleIcon,
} from '@heroicons/vue/24/outline'

defineProps({ authEnabled: Boolean })
defineEmits(['logout'])

const route = useRoute()
const isDark = ref(false)

const navItems = [
  { to: '/', label: '首页', icon: HomeIcon },
  { to: '/settings', label: '核心设置', icon: Cog6ToothIcon },
  { to: '/virtual', label: '虚拟媒体库', icon: RectangleStackIcon },
  { to: '/filters', label: '高级筛选器', icon: FunnelIcon },
  { to: '/libraries', label: '媒体库管理', icon: BuildingLibraryIcon },
]

const isActive = (path) => {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}

const toggleDark = () => {
  isDark.value = !isDark.value
  const html = document.documentElement
  if (isDark.value) {
    html.classList.add('dark')
    localStorage.setItem('theme', 'dark')
  } else {
    html.classList.remove('dark')
    localStorage.setItem('theme', 'light')
  }
}

onMounted(() => {
  const saved = localStorage.getItem('theme')
  const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches
  isDark.value = saved === 'dark' || (!saved && prefersDark)
})
</script>
