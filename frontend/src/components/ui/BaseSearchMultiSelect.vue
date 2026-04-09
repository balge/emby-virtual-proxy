<template>
  <div ref="rootRef" class="w-full" @focusin="onRootFocusIn" @focusout="onRootFocusOut">
    <label v-if="label" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
      {{ label }}
      <span v-if="required" class="text-red-500">*</span>
    </label>
    <div
      class="flex flex-wrap items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-2 py-1.5 mb-2 dark:border-gray-600 dark:bg-gray-800 focus-within:border-primary-500 focus-within:ring-2 focus-within:ring-primary-500/20"
      :class="{ 'opacity-60 pointer-events-none': disabled }"
    >
      <span
        v-for="id in normalizedIds"
        :key="id"
        class="inline-flex max-w-full items-center gap-0.5 rounded-md bg-gray-100 pl-2 pr-0.5 py-0.5 text-sm text-gray-800 dark:bg-gray-700 dark:text-gray-100"
      >
        <span class="truncate max-w-[14rem]">{{ chipLabel(id) }}</span>
        <button
          type="button"
          class="shrink-0 rounded p-0.5 text-gray-500 hover:bg-gray-200 hover:text-gray-800 dark:hover:bg-gray-600 dark:hover:text-gray-100"
          :disabled="disabled"
          aria-label="移除"
          @click.stop="removeId(id)"
        >
          <XMarkIcon class="h-4 w-4" />
        </button>
      </span>
      <div class="relative flex min-w-[6rem] flex-1 items-center gap-1">
        <input
          ref="inputRef"
          type="text"
          :value="search"
          :placeholder="placeholder"
          :disabled="disabled"
          :aria-expanded="panelOpen"
          aria-haspopup="listbox"
          autocomplete="off"
          class="min-w-0 flex-1 border-0 bg-transparent py-1 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-0 dark:text-gray-100 dark:placeholder:text-gray-500"
          @keydown.escape.prevent.stop="closePanel"
          @keydown.enter.prevent="onInputEnter"
          @input="onSearchInput"
        />
        <MagnifyingGlassIcon
          v-if="!hideSearchIcon"
          class="h-4 w-4 shrink-0 text-gray-400 dark:text-gray-500"
          aria-hidden="true"
        />
      </div>
    </div>
    <div
      v-if="panelOpen"
      ref="listRef"
      role="listbox"
      class="max-h-40 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900"
      @mousedown.prevent
      @scroll.passive="onListScroll"
    >
      <button
        v-if="allowManualEntry && manualEntryHint"
        type="button"
        role="option"
        class="flex w-full items-center gap-2 border-b border-gray-200 px-3 py-2 text-left text-sm text-primary-700 hover:bg-primary-50/80 dark:border-gray-700 dark:text-primary-300 dark:hover:bg-primary-900/20"
        @click="commitManualEntry"
      >
        <span class="min-w-0 truncate">{{ manualEntryActionLabel }}「{{ manualEntryHint }}」</span>
      </button>
      <button
        v-for="item in options"
        :key="item.id"
        type="button"
        role="option"
        :aria-selected="isSelected(item)"
        :disabled="disabled"
        class="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors disabled:opacity-50 disabled:pointer-events-none"
        :class="
          isSelected(item)
            ? 'bg-primary-50 text-primary-800 dark:bg-primary-900/30 dark:text-primary-200'
            : 'text-gray-700 hover:bg-primary-50/80 dark:text-gray-300 dark:hover:bg-primary-900/20'
        "
        @click="toggle(item)"
      >
        <span class="min-w-0 truncate font-medium">{{ labelOf(item) }}</span>
        <CheckIcon
          v-if="isSelected(item)"
          class="h-5 w-5 shrink-0 text-primary-600 dark:text-primary-400"
          aria-hidden="true"
        />
      </button>
      <p v-if="!options.length && !loadingMore && !(allowManualEntry && manualEntryHint)" class="px-3 py-4 text-center text-xs text-gray-400">{{ emptyText }}</p>
      <p v-if="loadingMore" class="px-3 py-2 text-center text-xs text-gray-400 dark:text-gray-500">加载中…</p>
    </div>
    <p v-if="hint" class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ hint }}</p>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick, onBeforeUnmount } from 'vue'
import { MagnifyingGlassIcon, CheckIcon, XMarkIcon } from '@heroicons/vue/20/solid'

const props = defineProps({
  /** Selected item ids */
  modelValue: { type: Array, default: () => [] },
  search: { type: String, default: '' },
  options: { type: Array, default: () => [] },
  itemLabel: { type: Function, default: null },
  /** (id) => label for chips; falls back to id */
  resolveLabel: { type: Function, default: null },
  label: String,
  placeholder: { type: String, default: '搜索...' },
  emptyText: { type: String, default: '无结果' },
  hint: String,
  required: Boolean,
  disabled: Boolean,
  /** 是否还有更多项（滚动到底触发 load-more） */
  hasMore: { type: Boolean, default: false },
  loadingMore: { type: Boolean, default: false },
  /**
   * 允许将输入框中的文本直接加入已选（Enter 或点「添加」），不依赖远程搜索接口。
   * 仍可通过 update:search / search 在父组件内做本地 options 过滤。
   */
  allowManualEntry: { type: Boolean, default: false },
  /** 与 allowManualEntry 搭配：隐藏搜索图标，表示仅本地筛选 / 手输 */
  hideSearchIcon: { type: Boolean, default: false },
  /**
   * 最多可选几项；不设置或 ≤0 表示不限制。
   * 为 1 时等价单选：再选一项会替换当前项；手动输入也会替换。
   */
  maxSelections: { type: Number, default: null },
})

const emit = defineEmits(['update:modelValue', 'update:search', 'search', 'load-more'])

const rootRef = ref(null)
const inputRef = ref(null)
const listRef = ref(null)
const panelOpen = ref(false)
let focusOutTimer = null

const loadMoreArmed = ref(true)
const SCROLL_LOAD_THRESHOLD_PX = 48

const effectiveMaxSelections = computed(() => {
  const m = props.maxSelections
  return m != null && Number(m) > 0 ? Math.floor(Number(m)) : null
})

watch(
  () => props.disabled,
  (d) => {
    if (d) panelOpen.value = false
  },
)

watch(
  () => props.loadingMore,
  (loading, wasLoading) => {
    if (wasLoading && !loading) loadMoreArmed.value = true
  },
)

watch(panelOpen, (open) => {
  if (open) loadMoreArmed.value = true
})

watch(
  () => props.options.length,
  async () => {
    loadMoreArmed.value = true
    await nextTick()
    const el = listRef.value
    if (!el || !props.hasMore || props.loadingMore || props.disabled) return
    if (
      el.scrollHeight <= el.clientHeight ||
      el.scrollTop + el.clientHeight >= el.scrollHeight - SCROLL_LOAD_THRESHOLD_PX
    ) {
      loadMoreArmed.value = false
      emit('load-more')
    }
  },
)

onBeforeUnmount(() => {
  if (focusOutTimer) clearTimeout(focusOutTimer)
})

/** 仅搜索框获得焦点时展开 */
function onRootFocusIn(e) {
  if (props.disabled) return
  if (focusOutTimer) {
    clearTimeout(focusOutTimer)
    focusOutTimer = null
  }
  if (inputRef.value && e.target === inputRef.value) {
    panelOpen.value = true
  }
}

/**
 * 焦点离开组件，或移到标签删除按钮等区域时收起。
 * 用 microtask 读取 document.activeElement：点击选项时焦点可能先落到按钮上，仍在 listRef 内，不应误关。
 */
function onRootFocusOut() {
  if (props.disabled) return
  if (focusOutTimer) clearTimeout(focusOutTimer)
  focusOutTimer = window.setTimeout(() => {
    focusOutTimer = null
    const root = rootRef.value
    const inputEl = inputRef.value
    const listEl = listRef.value
    const ae = document.activeElement
    if (!root) return
    if (!ae || !root.contains(ae)) {
      panelOpen.value = false
      return
    }
    if (ae === inputEl) return
    if (listEl && listEl.contains(ae)) return
    panelOpen.value = false
  }, 0)
}

function closePanel() {
  if (focusOutTimer) {
    clearTimeout(focusOutTimer)
    focusOutTimer = null
  }
  panelOpen.value = false
}

function onListScroll(e) {
  const el = e.target
  if (!el || props.disabled) return
  const nearBottom =
    el.scrollTop + el.clientHeight >= el.scrollHeight - SCROLL_LOAD_THRESHOLD_PX
  if (!nearBottom) {
    loadMoreArmed.value = true
    return
  }
  if (!loadMoreArmed.value) return
  if (!props.hasMore || props.loadingMore) return
  loadMoreArmed.value = false
  emit('load-more')
}

const normalizedIds = computed(() =>
  (props.modelValue || []).map((x) => String(x)).filter(Boolean),
)

const manualEntryHint = computed(() => {
  if (!props.allowManualEntry) return ''
  const raw = String(props.search || '').trim()
  if (!raw) return ''
  const lower = raw.toLowerCase()
  const taken = new Set(normalizedIds.value.map((id) => id.toLowerCase()))
  if (taken.has(lower)) return ''
  const dupOption = props.options.some(
    (item) => String(labelOf(item)).trim().toLowerCase() === lower,
  )
  if (dupOption) return ''
  return raw
})

const manualEntryActionLabel = computed(() => {
  if (effectiveMaxSelections.value === 1 && normalizedIds.value.length >= 1) {
    return '改用'
  }
  return '添加'
})

function labelOf(item) {
  if (props.itemLabel) return props.itemLabel(item) ?? ''
  return item?.name ?? String(item?.id ?? '')
}

function chipLabel(id) {
  if (props.resolveLabel) return props.resolveLabel(id) ?? String(id)
  return String(id)
}

function isSelected(item) {
  const id = String(item.id)
  return normalizedIds.value.includes(id)
}

function onSearchInput(e) {
  const v = e.target.value
  emit('update:search', v)
  emit('search', v)
}

function onInputEnter() {
  if (props.allowManualEntry) commitManualEntry()
}

function commitManualEntry() {
  if (!props.allowManualEntry || props.disabled) return
  const raw = String(props.search || '').trim()
  if (!raw) return
  const lower = raw.toLowerCase()
  const cur = [...normalizedIds.value]
  if (cur.some((id) => id.toLowerCase() === lower)) return
  const max = effectiveMaxSelections.value
  if (max === 1) {
    emit('update:modelValue', [raw])
  } else {
    if (max != null && cur.length >= max) return
    cur.push(raw)
    emit('update:modelValue', cur)
  }
  emit('update:search', '')
  emit('search', '')
}

function toggle(item) {
  if (props.disabled) return
  const id = String(item.id)
  const cur = [...normalizedIds.value]
  const i = cur.indexOf(id)
  const max = effectiveMaxSelections.value
  if (i >= 0) {
    cur.splice(i, 1)
    emit('update:modelValue', cur)
  } else if (max === 1) {
    emit('update:modelValue', [id])
  } else {
    if (max != null && cur.length >= max) return
    cur.push(id)
    emit('update:modelValue', cur)
  }
  emit('update:search', '')
  emit('search', '')
}

function removeId(id) {
  if (props.disabled) return
  const s = String(id)
  emit(
    'update:modelValue',
    (props.modelValue || []).filter((x) => String(x) !== s),
  )
}
</script>
