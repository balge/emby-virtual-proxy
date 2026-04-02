<template>
  <BaseDialog :open="store.layoutManagerVisible" title="调整主页布局" size="xl" @close="store.layoutManagerVisible = false">
    <div class="flex flex-col md:flex-row gap-4 min-h-[350px]">
      <!-- Displayed -->
      <div class="flex-1 flex flex-col min-w-0">
        <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">已显示（按顺序）</h3>
        <div class="flex-1 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 p-3 overflow-y-auto">
          <draggable v-model="displayedLibs" group="libs" item-key="id" class="space-y-2 min-h-[200px]">
            <template #item="{ element }">
              <div class="flex items-center gap-2 px-3 py-2.5 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 cursor-grab active:cursor-grabbing shadow-sm hover:shadow transition-shadow">
                <BaseTag :variant="element.type === 'real' ? 'primary' : 'success'">{{ element.type === 'real' ? '真实' : '虚拟' }}</BaseTag>
                <span class="text-sm text-gray-700 dark:text-gray-300 truncate">{{ element.name }}</span>
              </div>
            </template>
          </draggable>
        </div>
      </div>
      <!-- Hidden -->
      <div class="flex-1 flex flex-col min-w-0">
        <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">未显示</h3>
        <div class="flex-1 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 p-3 overflow-y-auto">
          <draggable v-model="hiddenLibs" group="libs" item-key="id" class="space-y-2 min-h-[200px]">
            <template #item="{ element }">
              <div class="flex items-center gap-2 px-3 py-2.5 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 cursor-grab active:cursor-grabbing shadow-sm hover:shadow transition-shadow">
                <BaseTag :variant="element.type === 'real' ? 'primary' : 'success'">{{ element.type === 'real' ? '真实' : '虚拟' }}</BaseTag>
                <span class="text-sm text-gray-700 dark:text-gray-300 truncate">{{ element.name }}</span>
              </div>
            </template>
          </draggable>
        </div>
      </div>
    </div>

    <template #footer>
      <BaseButton @click="store.layoutManagerVisible = false">取消</BaseButton>
      <BaseButton variant="primary" :loading="store.saving" @click="saveLayout">保存布局</BaseButton>
    </template>
  </BaseDialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import draggable from 'vuedraggable'
import { useMainStore } from '@/stores/main'
import BaseDialog from '@/components/ui/BaseDialog.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseTag from '@/components/ui/BaseTag.vue'

const store = useMainStore()
const displayedLibs = ref([])
const hiddenLibs = ref([])

watch(() => store.layoutManagerVisible, (val) => {
  if (val) {
    const allMap = new Map(store.allLibrariesForSorting.map(l => [l.id, l]))
    const displayedIds = new Set(store.config.display_order || [])
    displayedLibs.value = (store.config.display_order || []).map(id => allMap.get(id)).filter(Boolean)
    hiddenLibs.value = store.allLibrariesForSorting.filter(l => !displayedIds.has(l.id))
  }
}, { immediate: true })

const saveLayout = async () => {
  await store.saveDisplayOrder(displayedLibs.value.map(l => l.id))
  store.layoutManagerVisible = false
}
</script>
