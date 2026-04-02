<template>
  <div class="relative inline-block cursor-pointer min-w-[140px]" @click="openPicker">
    <div class="relative">
      <input
        ref="inputRef"
        type="date"
        :value="modelValue"
        :disabled="disabled"
        :placeholder="placeholder"
        class="date-input block w-full h-9 rounded-lg border border-gray-300 bg-white pl-9 pr-3 py-2 text-sm text-gray-900 transition-colors focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 focus:outline-none disabled:bg-gray-100 disabled:text-gray-400 disabled:cursor-not-allowed dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:disabled:bg-gray-900 dark:disabled:text-gray-500 cursor-pointer"
        @input="$emit('update:modelValue', $event.target.value)"
      />
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
  placeholder: { type: String, default: '选择日期' },
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
/* Prevent iOS Safari collapse when empty */
.date-input {
  min-height: 36px;
  line-height: 1.25;
  -webkit-appearance: none;
  -moz-appearance: none;
  appearance: none;
}

/* Show placeholder text when no value on iOS */
.date-input:not(:valid):not(:focus) {
  color: #9ca3af;
}
.date-input:not(:valid):not(:focus)::before {
  content: attr(placeholder);
  color: #9ca3af;
  position: absolute;
  left: 2.25rem;
  top: 50%;
  transform: translateY(-50%);
  pointer-events: none;
}

/* Hide native calendar picker indicator */
.date-input::-webkit-calendar-picker-indicator {
  opacity: 0;
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  cursor: pointer;
}
.date-input::-webkit-inner-spin-button {
  display: none;
}
.date-input::-webkit-date-and-time-value {
  text-align: left;
}
</style>
