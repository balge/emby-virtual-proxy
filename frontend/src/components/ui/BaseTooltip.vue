<template>
  <div class="relative inline-flex" @mouseenter="show = true" @mouseleave="show = false" @focusin="show = true" @focusout="show = false">
    <slot />
    <Transition
      enter-active-class="transition ease-out duration-100"
      enter-from-class="opacity-0 translate-y-1"
      enter-to-class="opacity-100 translate-y-0"
      leave-active-class="transition ease-in duration-75"
      leave-from-class="opacity-100 translate-y-0"
      leave-to-class="opacity-0 translate-y-1"
    >
      <div
        v-if="show && text"
        class="absolute z-50 px-2.5 py-1.5 text-xs font-medium text-white bg-gray-900 dark:bg-gray-700 rounded-lg shadow-lg max-w-xs pointer-events-none"
        :class="positionClass"
      >
        {{ text }}
        <div class="absolute w-2 h-2 bg-gray-900 dark:bg-gray-700 rotate-45" :class="arrowClass" />
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  text: String,
  position: { type: String, default: 'top' },
})

const show = ref(false)

const positionClass = computed(() => ({
  top: 'bottom-full left-1/2 -translate-x-1/2 mb-2',
  bottom: 'top-full left-1/2 -translate-x-1/2 mt-2',
}[props.position] || 'bottom-full left-1/2 -translate-x-1/2 mb-2'))

const arrowClass = computed(() => ({
  top: '-bottom-1 left-1/2 -translate-x-1/2',
  bottom: '-top-1 left-1/2 -translate-x-1/2',
}[props.position] || '-bottom-1 left-1/2 -translate-x-1/2'))
</script>
