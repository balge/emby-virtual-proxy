<template>
  <div class="w-full">
    <label v-if="label" class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
      {{ label }}
      <span v-if="required" class="text-red-500">*</span>
    </label>
    <input
      type="text"
      :value="search"
      :placeholder="placeholder"
      :disabled="disabled"
      class="block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 focus:outline-none mb-2 disabled:bg-gray-100 disabled:text-gray-500 dark:disabled:bg-gray-900"
      @input="onSearchInput"
    />
    <div
      class="max-h-40 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900"
    >
      <button
        v-for="item in options"
        :key="item.id"
        type="button"
        :disabled="disabled"
        class="w-full text-left px-3 py-2 text-sm hover:bg-primary-50 dark:hover:bg-primary-900/20 transition-colors disabled:opacity-50 disabled:pointer-events-none"
        :class="
          isSelected(item)
            ? 'bg-primary-50 text-primary-700 dark:bg-primary-900/30 dark:text-primary-300'
            : 'text-gray-700 dark:text-gray-300'
        "
        @click="select(item)"
      >
        {{ labelOf(item) }}
      </button>
      <p v-if="!options.length" class="px-3 py-4 text-center text-xs text-gray-400">{{ emptyText }}</p>
    </div>
    <p v-if="hint" class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ hint }}</p>
  </div>
</template>

<script setup>
const props = defineProps({
  modelValue: { type: [String, Number, null], default: '' },
  /** Bound to the search input (use v-model:search). */
  search: { type: String, default: '' },
  /** Items currently shown in the list; filtering / remote load is up to the parent. */
  options: { type: Array, default: () => [] },
  /** (item) => display string; defaults to item.name */
  itemLabel: { type: Function, default: null },
  label: String,
  placeholder: { type: String, default: '搜索...' },
  emptyText: { type: String, default: '无结果' },
  hint: String,
  required: Boolean,
  disabled: Boolean,
})

const emit = defineEmits(['update:modelValue', 'update:search', 'search'])

function labelOf(item) {
  if (props.itemLabel) return props.itemLabel(item) ?? ''
  return item?.name ?? String(item?.id ?? '')
}

function isSelected(item) {
  return props.modelValue === item.id || String(props.modelValue) === String(item.id)
}

function onSearchInput(e) {
  const v = e.target.value
  emit('update:search', v)
  emit('search', v)
}

function select(item) {
  if (props.disabled) return
  emit('update:modelValue', item.id)
  emit('update:search', labelOf(item) || '')
}
</script>
