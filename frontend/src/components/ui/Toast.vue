<template>
  <div class="fixed top-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none">
    <TransitionGroup name="slide">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        class="pointer-events-auto px-4 py-3 rounded-xl shadow-lg text-sm font-medium flex items-center gap-2 min-w-[260px] max-w-[400px] backdrop-blur-sm"
        :class="toastClass(toast.type)"
      >
        <component :is="toastIcon(toast.type)" class="w-5 h-5 shrink-0" />
        <span class="flex-1">{{ toast.message }}</span>
        <button @click="remove(toast.id)" class="shrink-0 opacity-60 hover:opacity-100 transition-opacity">
          <XMarkIcon class="w-4 h-4" />
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup>
import { useToast } from '@/composables/useToast'
import { XMarkIcon, CheckCircleIcon, ExclamationTriangleIcon, XCircleIcon, InformationCircleIcon } from '@heroicons/vue/20/solid'

const { toasts, remove } = useToast()

const toastClass = (type) => ({
  success: 'bg-emerald-500/90 text-white',
  error: 'bg-red-500/90 text-white',
  warning: 'bg-amber-500/90 text-white',
  info: 'bg-blue-500/90 text-white',
}[type] || 'bg-gray-700/90 text-white')

const toastIcon = (type) => ({
  success: CheckCircleIcon,
  error: XCircleIcon,
  warning: ExclamationTriangleIcon,
  info: InformationCircleIcon,
}[type] || InformationCircleIcon)
</script>
