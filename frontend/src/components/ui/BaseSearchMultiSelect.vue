<template>
  <div class="w-full">
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
          type="text"
          :value="search"
          :placeholder="placeholder"
          :disabled="disabled"
          class="min-w-0 flex-1 border-0 bg-transparent py-1 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-0 dark:text-gray-100 dark:placeholder:text-gray-500"
          @input="onSearchInput"
        />
        <MagnifyingGlassIcon class="h-4 w-4 shrink-0 text-gray-400 dark:text-gray-500" aria-hidden="true" />
      </div>
    </div>
    <div
      class="max-h-40 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900"
    >
      <button
        v-for="item in options"
        :key="item.id"
        type="button"
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
      <p v-if="!options.length" class="px-3 py-4 text-center text-xs text-gray-400">{{ emptyText }}</p>
    </div>
    <p v-if="hint" class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ hint }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
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
})

const emit = defineEmits(['update:modelValue', 'update:search', 'search'])

const normalizedIds = computed(() =>
  (props.modelValue || []).map((x) => String(x)).filter(Boolean),
)

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

function toggle(item) {
  if (props.disabled) return
  const id = String(item.id)
  const cur = [...normalizedIds.value]
  const i = cur.indexOf(id)
  if (i >= 0) cur.splice(i, 1)
  else cur.push(id)
  emit('update:modelValue', cur)
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
