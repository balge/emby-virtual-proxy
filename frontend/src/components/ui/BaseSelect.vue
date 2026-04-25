<template>
  <div class="w-full" :class="wrapperClass">
    <label
      v-if="label"
      class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
    >
      {{ label }}
      <span v-if="required" class="text-red-500">*</span>
    </label>
    <div class="relative w-full">
      <button
        ref="buttonRef"
        type="button"
        :disabled="disabled"
        :aria-expanded="open"
        aria-haspopup="listbox"
        :class="triggerClasses"
        @click="toggle"
        @keydown.escape.prevent="close"
        @keydown.down.prevent="openAndFocusFirst"
      >
        <span class="min-w-0 flex flex-1 items-center gap-2 text-left">
          <img
            v-if="selectedOption?.preview"
            :src="selectedOption.preview"
            :alt="selectedOption.label"
            class="h-6 w-10 shrink-0 rounded object-cover border border-gray-200 dark:border-gray-600"
          />
          <span class="min-w-0 flex-1 truncate" :class="labelTextClass">{{ displayLabel }}</span>
        </span>
        <ChevronDownIcon
          class="h-4 w-4 shrink-0 text-gray-500 dark:text-gray-400 transition-transform"
          :class="{ 'rotate-180': open }"
          aria-hidden="true"
        />
      </button>

      <Teleport to="body">
        <div
          v-if="open"
          ref="menuRef"
          role="listbox"
          class="fixed max-h-60 overflow-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-600 dark:bg-gray-800"
          :style="menuStyle"
        >
          <button
            v-for="(opt, idx) in options"
            :key="optionKey(opt.value, idx)"
            type="button"
            role="option"
            :aria-selected="isSelected(opt.value)"
            class="flex w-full px-3 py-2 text-left text-sm transition-colors"
            :class="[
              opt.preview ? 'relative flex-col items-start' : 'items-center',
              optionRowClass(opt.value, idx),
            ]"
            @click.stop="selectOption(opt.value)"
            @mouseenter="highlightIndex = idx"
          >
            <img
              v-if="opt.preview"
              :src="opt.preview"
              :alt="opt.label"
              class="mb-1.5 max-h-40 w-full rounded object-contain border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-900"
            />
            <span class="min-w-0 w-full truncate">{{ opt.label }}</span>
            <CheckIcon
              v-if="isSelected(opt.value)"
              class="ml-2 h-4 w-4 shrink-0 text-primary-600 dark:text-primary-400"
              :class="opt.preview ? 'absolute right-3 top-3' : ''"
              aria-hidden="true"
            />
          </button>
        </div>
      </Teleport>
    </div>
    <p v-if="hint" class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ hint }}</p>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { ChevronDownIcon, CheckIcon } from '@heroicons/vue/20/solid'

const props = defineProps({
  modelValue: { type: [String, Number, Boolean, null], default: null },
  /** { value, label }；value 可用 null 表示「空 / 默认」项 */
  options: { type: Array, default: () => [] },
  label: String,
  placeholder: { type: String, default: '请选择' },
  disabled: Boolean,
  required: Boolean,
  hint: String,
  size: { type: String, default: 'md' },
  /** 外层 div 额外 class（如 w-full sm:w-48） */
  wrapperClass: { type: String, default: '' },
  /** 触发按钮额外 class */
  buttonClass: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const buttonRef = ref(null)
const menuRef = ref(null)
const menuStyle = ref({})
const highlightIndex = ref(-1)

const sizeClass = computed(() =>
  props.size === 'sm'
    ? 'min-h-9 px-2.5 py-1.5 text-sm gap-2'
    : 'min-h-10 px-3 py-2 text-sm gap-2',
)

const baseButtonStyles =
  'inline-flex w-full items-center justify-between rounded-lg border border-gray-300 bg-white transition-colors focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500/20 disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-500 dark:border-gray-600 dark:bg-gray-800 dark:disabled:bg-gray-900 dark:disabled:text-gray-500'

const triggerClasses = computed(() =>
  [baseButtonStyles, sizeClass.value, props.buttonClass].filter(Boolean),
)

const displayLabel = computed(() => {
  const o = props.options.find((x) => valuesEqual(x.value, props.modelValue))
  if (o) return o.label
  return props.placeholder
})

const selectedOption = computed(() =>
  props.options.find((x) => valuesEqual(x.value, props.modelValue)),
)

/** 当前值未对应任何 option 时展示 placeholder（含空字符串），用浅色与已选项区分 */
const isPlaceholderState = computed(
  () => !props.options.some((x) => valuesEqual(x.value, props.modelValue)),
)

const labelTextClass = computed(() => {
  if (props.disabled) return 'text-gray-500 dark:text-gray-500'
  if (isPlaceholderState.value) return 'text-gray-400 dark:text-gray-500'
  return 'text-gray-900 dark:text-gray-100'
})

function valuesEqual(a, b) {
  if (a === b) return true
  if (a === null || a === undefined) return b === null || b === undefined
  if (b === null || b === undefined) return false
  return String(a) === String(b)
}

function isSelected(val) {
  return valuesEqual(val, props.modelValue)
}

function optionKey(val, idx) {
  if (val === null || val === undefined) return `__null__${idx}`
  return String(val)
}

function optionRowClass(val, idx) {
  const selected = isSelected(val)
  const hi = highlightIndex.value === idx
  if (selected) {
    return hi
      ? 'bg-primary-100 text-primary-900 dark:bg-primary-900/40 dark:text-primary-100'
      : 'bg-primary-50 text-primary-800 dark:bg-primary-950/50 dark:text-primary-200'
  }
  return hi
    ? 'bg-gray-100 text-gray-900 dark:bg-gray-700/80 dark:text-gray-100'
    : 'text-gray-800 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700/50'
}

function updateMenuPosition() {
  const el = buttonRef.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const gap = 4
  const maxH = 240
  let top = r.bottom + gap
  const spaceBelow = window.innerHeight - top - 8
  const spaceAbove = r.top - 8
  let maxHeight = maxH
  if (spaceBelow < 120 && spaceAbove > spaceBelow) {
    maxHeight = Math.min(maxH, spaceAbove - gap)
    top = Math.max(8, r.top - gap - maxHeight)
  } else {
    maxHeight = Math.min(maxH, spaceBelow)
  }
  menuStyle.value = {
    top: `${top}px`,
    left: `${r.left}px`,
    width: `${r.width}px`,
    maxHeight: `${maxHeight}px`,
    zIndex: 400,
  }
}

let scrollListenerAttached = false
function attachGlobalListeners() {
  if (scrollListenerAttached) return
  scrollListenerAttached = true
  window.addEventListener('scroll', updateMenuPosition, true)
  window.addEventListener('resize', updateMenuPosition)
  document.addEventListener('click', onDocClick, true)
}

function detachGlobalListeners() {
  if (!scrollListenerAttached) return
  scrollListenerAttached = false
  window.removeEventListener('scroll', updateMenuPosition, true)
  window.removeEventListener('resize', updateMenuPosition)
  document.removeEventListener('click', onDocClick, true)
}

function onDocClick(e) {
  if (!open.value) return
  const path = typeof e.composedPath === 'function' ? e.composedPath() : []
  if (buttonRef.value && path.includes(buttonRef.value)) return
  if (menuRef.value && path.includes(menuRef.value)) return
  const t = e.target
  if (buttonRef.value?.contains(t) || menuRef.value?.contains(t)) return
  close()
}

function close() {
  open.value = false
  highlightIndex.value = -1
}

function toggle() {
  if (props.disabled) return
  open.value = !open.value
}

function openAndFocusFirst() {
  if (props.disabled) return
  open.value = true
  highlightIndex.value = 0
}

function selectOption(val) {
  emit('update:modelValue', val)
  close()
}

watch(open, (v) => {
  if (v) {
    highlightIndex.value = props.options.findIndex((o) =>
      valuesEqual(o.value, props.modelValue),
    )
    if (highlightIndex.value < 0) highlightIndex.value = 0
    nextTick(() => {
      updateMenuPosition()
      attachGlobalListeners()
    })
  } else {
    detachGlobalListeners()
  }
})

onBeforeUnmount(() => {
  detachGlobalListeners()
})
</script>
