<template>
  <!-- Top bar -->
  <div class="lg:hidden fixed top-0 inset-x-0 h-14 bg-white/80 dark:bg-gray-900/80 backdrop-blur-md border-b border-gray-200 dark:border-gray-800 z-30 flex items-center justify-between px-4">
    <div class="flex items-center gap-2">
      <img src="/favicon.png" alt="Logo" class="w-7 h-7 rounded-md" />
      <span class="font-semibold text-gray-900 dark:text-gray-100 text-sm">Emby Proxy</span>
    </div>
    <div class="flex items-center gap-2">
      <button @click="toggleDark" class="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800">
        <MoonIcon v-if="!isDark" class="w-5 h-5" />
        <SunIcon v-else class="w-5 h-5" />
      </button>
      <button
        v-if="authEnabled"
        @click="$emit('logout')"
        class="p-2 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
      >
        <ArrowRightStartOnRectangleIcon class="w-5 h-5" />
      </button>
    </div>
  </div>

  <!-- Bottom tab bar -->
  <nav class="lg:hidden fixed bottom-0 inset-x-0 bg-white/80 dark:bg-gray-900/80 backdrop-blur-md border-t border-gray-200 dark:border-gray-800 z-30 flex justify-around py-1 safe-bottom">
    <router-link
      v-for="item in navItems"
      :key="item.to"
      :to="item.to"
      class="flex flex-col items-center gap-0.5 px-2 py-1.5 text-[10px] font-medium transition-colors min-w-0"
      :class="isActive(item.to)
        ? 'text-primary-600 dark:text-primary-400'
        : 'text-gray-500 dark:text-gray-400'"
    >
      <component :is="item.icon" class="w-5 h-5" />
      <span class="truncate">{{ item.label }}</span>
    </router-link>
  </nav>
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
  { to: '/settings', label: '设置', icon: Cog6ToothIcon },
  { to: '/virtual', label: '虚拟库', icon: RectangleStackIcon },
  { to: '/filters', label: '筛选器', icon: FunnelIcon },
  { to: '/libraries', label: '媒体库', icon: BuildingLibraryIcon },
]

const isActive = (path) => {
  if (path === '/') return route.path === '/'
  return route.path.startsWith(path)
}

const toggleDark = () => {
  isDark.value = !isDark.value
  const html = document.documentElement
  html.classList.toggle('dark', isDark.value)
  localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
}

onMounted(() => {
  const saved = localStorage.getItem('theme')
  const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches
  isDark.value = saved === 'dark' || (!saved && prefersDark)
})
</script>

<style scoped>
.safe-bottom {
  padding-bottom: max(0.25rem, env(safe-area-inset-bottom));
}
</style>
