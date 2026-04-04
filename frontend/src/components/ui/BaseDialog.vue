<template>
  <TransitionRoot :show="open" as="template">
    <Dialog class="relative z-50" @close="$emit('close')">
      <TransitionChild
        enter="ease-out duration-200" enter-from="opacity-0" enter-to="opacity-100"
        leave="ease-in duration-150" leave-from="opacity-100" leave-to="opacity-0"
      >
        <div class="fixed inset-0 bg-black/40 backdrop-blur-sm" />
      </TransitionChild>

      <div class="fixed inset-0 overflow-y-auto">
        <div class="flex min-h-full items-center justify-center p-4">
          <TransitionChild
            enter="ease-out duration-200" enter-from="opacity-0 scale-95" enter-to="opacity-100 scale-100"
            leave="ease-in duration-150" leave-from="opacity-100 scale-100" leave-to="opacity-0 scale-95"
          >
            <DialogPanel
              class="w-[90vw] max-w-[90vw] min-w-0 sm:w-full rounded-2xl bg-white shadow-xl dark:bg-gray-800 overflow-hidden"
              :class="widthClass"
            >
              <div v-if="title" class="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
                <DialogTitle class="text-lg font-semibold text-gray-900 dark:text-gray-100">
                  {{ title }}
                </DialogTitle>
              </div>
              <div class="px-6 py-4" :class="bodyClass">
                <slot />
              </div>
              <div v-if="$slots.footer" class="px-6 py-3 border-t border-gray-200 dark:border-gray-700 flex justify-end gap-2">
                <slot name="footer" />
              </div>
            </DialogPanel>
          </TransitionChild>
        </div>
      </div>
    </Dialog>
  </TransitionRoot>
</template>

<script setup>
import { computed } from 'vue'
import { Dialog, DialogPanel, DialogTitle, TransitionRoot, TransitionChild } from '@headlessui/vue'

const props = defineProps({
  open: Boolean,
  title: String,
  size: { type: String, default: 'md' },
  bodyClass: { type: String, default: '' },
})

defineEmits(['close'])

const widthClass = computed(() => ({
  sm: 'sm:max-w-md',
  md: 'sm:max-w-lg',
  lg: 'sm:max-w-2xl',
  xl: 'sm:max-w-4xl',
  full: 'sm:max-w-6xl',
}[props.size] || 'sm:max-w-lg'))
</script>
