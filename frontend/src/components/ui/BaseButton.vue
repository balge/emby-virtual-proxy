<template>
  <button
    :type="htmlType"
    :disabled="disabled || loading"
    :class="[baseClass, variantClass, sizeClass, { 'opacity-50 cursor-not-allowed': disabled || loading }]"
    class="inline-flex items-center justify-center gap-1.5 font-medium rounded-lg transition-all duration-150 focus:outline-none focus:ring-2 focus:ring-offset-2 dark:focus:ring-offset-gray-900 cursor-pointer disabled:cursor-not-allowed"
  >
    <svg v-if="loading" class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
    <slot />
  </button>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  variant: { type: String, default: 'secondary' },
  size: { type: String, default: 'md' },
  disabled: Boolean,
  loading: Boolean,
  htmlType: { type: String, default: 'button' },
})

const baseClass = ''

const variantClass = computed(() => ({
  primary: 'bg-primary-600 text-white hover:bg-primary-700 focus:ring-primary-500 shadow-sm',
  secondary: 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50 focus:ring-primary-500 shadow-sm dark:bg-gray-800 dark:text-gray-200 dark:border-gray-600 dark:hover:bg-gray-700',
  danger: 'bg-red-600 text-white hover:bg-red-700 focus:ring-red-500 shadow-sm',
  'danger-outline': 'text-red-600 border border-red-300 hover:bg-red-50 focus:ring-red-500 dark:text-red-400 dark:border-red-700 dark:hover:bg-red-950',
  success: 'bg-emerald-600 text-white hover:bg-emerald-700 focus:ring-emerald-500 shadow-sm',
  'success-outline': 'text-emerald-600 border border-emerald-300 hover:bg-emerald-50 focus:ring-emerald-500 dark:text-emerald-400 dark:border-emerald-700 dark:hover:bg-emerald-950',
  warning: 'bg-amber-500 text-white hover:bg-amber-600 focus:ring-amber-500 shadow-sm',
  ghost: 'text-gray-600 hover:bg-gray-100 focus:ring-primary-500 dark:text-gray-300 dark:hover:bg-gray-800',
}[props.variant] || ''))

const sizeClass = computed(() => ({
  xs: 'px-2 py-1 text-xs',
  sm: 'px-2.5 py-1.5 text-xs',
  md: 'px-3 py-2 text-sm',
  lg: 'px-4 py-2.5 text-sm',
}[props.size] || ''))
</script>
