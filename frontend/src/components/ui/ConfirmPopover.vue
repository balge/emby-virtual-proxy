<template>
  <div class="relative inline-block">
    <div @click="showConfirm = !showConfirm">
      <slot name="trigger" />
    </div>
    <Transition
      enter-active-class="transition ease-out duration-100"
      enter-from-class="opacity-0 scale-95"
      enter-to-class="opacity-100 scale-100"
      leave-active-class="transition ease-in duration-75"
      leave-from-class="opacity-100 scale-100"
      leave-to-class="opacity-0 scale-95"
    >
      <div
        v-if="showConfirm"
        class="absolute z-50 bottom-full mb-2 right-0 w-64 rounded-xl bg-white p-4 shadow-xl ring-1 ring-gray-200 dark:bg-gray-800 dark:ring-gray-700"
      >
        <p class="text-sm text-gray-700 dark:text-gray-300 mb-3">{{ message }}</p>
        <div class="flex justify-end gap-2">
          <button
            class="px-3 py-1.5 text-xs font-medium rounded-lg text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700 transition-colors"
            @click="showConfirm = false"
          >
            {{ cancelText }}
          </button>
          <button
            class="px-3 py-1.5 text-xs font-medium rounded-lg text-white bg-red-600 hover:bg-red-700 transition-colors"
            @click="onConfirm"
          >
            {{ confirmText }}
          </button>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  message: { type: String, default: '确定执行此操作？' },
  confirmText: { type: String, default: '确定' },
  cancelText: { type: String, default: '取消' },
})

const emit = defineEmits(['confirm'])
const showConfirm = ref(false)

const onConfirm = () => {
  showConfirm.value = false
  emit('confirm')
}
</script>
