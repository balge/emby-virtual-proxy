<template>
  <div
    class="inline-flex"
    @mouseenter="onEnter"
    @mouseleave="onLeave"
    @focusin="onEnter"
    @focusout="onLeave"
    ref="triggerRef"
  >
    <slot />
  </div>
  <Teleport to="body">
    <Transition
      enter-active-class="transition ease-out duration-100"
      enter-from-class="opacity-0 scale-95"
      enter-to-class="opacity-100 scale-100"
      leave-active-class="transition ease-in duration-75"
      leave-from-class="opacity-100 scale-100"
      leave-to-class="opacity-0 scale-95"
    >
      <div
        v-if="visible && text"
        ref="tooltipRef"
        class="fixed z-[9999] px-2.5 py-1.5 text-xs font-medium text-white bg-gray-900 dark:bg-gray-200 dark:text-gray-900 rounded-lg shadow-lg max-w-xs pointer-events-none"
        :style="floatingStyle"
      >
        {{ text }}
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { ref, nextTick } from 'vue'

const props = defineProps({
  text: String,
  position: { type: String, default: 'top' },
})

const visible = ref(false)
const triggerRef = ref(null)
const tooltipRef = ref(null)
const floatingStyle = ref({})

let hideTimer = null

const updatePosition = async () => {
  await nextTick()
  if (!triggerRef.value || !tooltipRef.value) return

  const triggerRect = triggerRef.value.getBoundingClientRect()
  const tooltipRect = tooltipRef.value.getBoundingClientRect()
  const gap = 6

  let top, left

  if (props.position === 'bottom') {
    top = triggerRect.bottom + gap
    left = triggerRect.left + triggerRect.width / 2 - tooltipRect.width / 2
  } else {
    top = triggerRect.top - tooltipRect.height - gap
    left = triggerRect.left + triggerRect.width / 2 - tooltipRect.width / 2
  }

  // Keep within viewport
  if (left < 4) left = 4
  if (left + tooltipRect.width > window.innerWidth - 4) {
    left = window.innerWidth - tooltipRect.width - 4
  }
  // If top position goes above viewport, flip to bottom
  if (top < 4) {
    top = triggerRect.bottom + gap
  }

  floatingStyle.value = {
    top: `${top}px`,
    left: `${left}px`,
  }
}

const onEnter = () => {
  clearTimeout(hideTimer)
  visible.value = true
  nextTick(updatePosition)
}

const onLeave = () => {
  hideTimer = setTimeout(() => {
    visible.value = false
  }, 50)
}
</script>
