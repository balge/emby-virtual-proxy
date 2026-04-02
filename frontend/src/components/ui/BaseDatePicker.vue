<template>
  <div class="relative inline-block cursor-pointer" @click="openPicker">
    <div class="relative">
      <input
        ref="inputRef"
        type="date"
        :value="modelValue"
        :disabled="disabled"
        class="block w-full rounded-lg border border-gray-300 bg-white pl-9 pr-3 py-2 text-sm text-gray-900 transition-colors focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 focus:outline-none disabled:bg-gray-100 disabled:text-gray-400 disabled:cursor-not-allowed dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900 dark:disabled:text-gray-500 cursor-pointer"
        style="-webkit-appearance: none; -moz-appearance: none;"
        @input="$emit('update:modelValue', $event.target.value)"
      />
      <!-- Hide native calendar icon completely -->
      <CalendarDaysIcon class="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-primary-500 dark:text-primary-400 pointer-events-none" />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { CalendarDaysIcon } from '@heroicons/vue/20/solid'

defineProps({
  modelValue: { type: String, default: '' },
  disabled: Boolean,
})

defineEmits(['update:modelValue'])

const inputRef = ref(null)

const openPicker = () => {
  if (inputRef.value && !inputRef.value.disabled) {
    inputRef.value.showPicker?.()
    inputRef.value.focus()
  }
}
</script>

<style scoped>
/* Hide native calendar picker indicator across browsers */
input[type="date"]::-webkit-calendar-picker-indicator {
  opacity: 0;
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  cursor: pointer;
}
input[type="date"]::-webkit-inner-spin-button {
  display: none;
}
</style>
