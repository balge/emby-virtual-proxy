<template>
  <div class="rounded-xl bg-gray-50 dark:bg-gray-700/40 p-4 transition-colors">
    <div class="flex items-center justify-between mb-1">
      <span class="text-sm font-medium text-gray-900 dark:text-gray-100">{{ label }}</span>
      <BaseSwitch :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)" />
    </div>
    <p class="text-xs text-gray-500 dark:text-gray-400">{{ desc }}</p>
  </div>
</template>

<script setup>
import BaseSwitch from '@/components/ui/BaseSwitch.vue'
defineProps({ label: String, desc: String, modelValue: Boolean })
defineEmits(['update:modelValue'])
</script>
