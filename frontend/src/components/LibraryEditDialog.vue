<template>
  <BaseDialog :open="store.dialogVisible" :title="store.isEditing ? '编辑虚拟库' : '添加虚拟库'" size="lg" @close="store.dialogVisible = false" body-class="max-h-[70vh] overflow-y-auto">
    <div class="space-y-4">
      <!-- Basic -->
      <BaseInput v-model="store.currentLibrary.name" label="虚拟库名称" placeholder="例如：豆瓣高分电影" required />

      <BaseSelect v-model="store.currentLibrary.resource_type" label="资源类型" required @update:model-value="onResourceTypeChange">
        <option value="all">全库 (All)</option>
        <option value="collection">合集 (Collection)</option>
        <option value="tag">标签 (Tag)</option>
        <option value="genre">类型 (Genre)</option>
        <option value="studio">工作室 (Studio)</option>
        <option value="person">人员 (Person)</option>
        <option value="rsshub">RSSHUB</option>
        <option value="random">偏好推荐 (Random)</option>
      </BaseSelect>

      <!-- RSS fields -->
      <template v-if="store.currentLibrary.resource_type === 'rsshub'">
        <BaseInput v-model="store.currentLibrary.rsshub_url" label="RSSHUB 链接" placeholder="https://rsshub.app/..." required />
        <BaseSelect v-model="store.currentLibrary.rss_type" label="RSS 类型" required>
          <option value="douban">豆瓣</option>
          <option value="bangumi">Bangumi</option>
        </BaseSelect>
        <div class="flex items-center gap-3">
          <label class="text-sm font-medium text-gray-700 dark:text-gray-300">开启数据保留</label>
          <BaseSwitch v-model="store.currentLibrary.enable_retention" />
        </div>
        <div v-if="store.currentLibrary.enable_retention">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">保留天数</label>
          <input type="number" v-model.number="store.currentLibrary.retention_days" min="0"
            class="block w-32 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 focus:outline-none" />
          <p class="mt-1 text-xs text-gray-500">0 表示永久保留。</p>
        </div>
        <BaseInput v-model="store.currentLibrary.fallback_tmdb_id" label="追加 TMDB ID" placeholder="可选" />
        <BaseSelect v-if="store.currentLibrary.fallback_tmdb_id" v-model="store.currentLibrary.fallback_tmdb_type" label="追加类型">
          <option value="Movie">电影</option>
          <option value="TV">电视剧</option>
        </BaseSelect>
      </template>

      <BaseSearchMultiSelect
        v-if="!['all', 'rsshub', 'random'].includes(store.currentLibrary.resource_type)"
        v-model="store.currentLibrary.resource_ids"
        :search="resourceSearch"
        :options="filteredResources"
        :item-label="resourceItemLabel"
        :resolve-label="resourceChipLabel"
        label="选择资源"
        required
        placeholder="搜索..."
        @update:search="setResourceSearch"
        @search="onResourceSearch"
      />

      <!-- Refresh interval -->
      <div>
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">刷新间隔（小时）</label>
        <input type="number" v-model.number="store.currentLibrary.cache_refresh_interval" min="0"
          class="block w-32 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 focus:outline-none" />
        <p class="mt-1 text-xs text-gray-500">留空使用全局配置。0 表示仅手动刷新。</p>
      </div>

      <!-- Advanced filter -->
      <BaseSelect v-model="store.currentLibrary.advanced_filter_id" label="高级筛选器" hint="留空表示不使用。">
        <option :value="null">无</option>
        <option v-for="f in store.config.advanced_filters" :key="f.id" :value="f.id">{{ f.name }}</option>
      </BaseSelect>

      <!-- TMDB merge -->
      <div class="flex items-center gap-3">
        <label class="text-sm font-medium text-gray-700 dark:text-gray-300">TMDB ID 合并</label>
        <BaseSwitch v-model="store.currentLibrary.merge_by_tmdb_id" />
        <span class="text-xs text-gray-500">同一 TMDB ID 只显示一个版本</span>
      </div>

      <!-- Source libraries -->
      <div v-if="store.currentLibrary.resource_type !== 'rsshub'">
        <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">源库范围</label>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-2">点击标签切换选中；不选则搜索全部真实库。</p>
        <div
          class="max-h-36 overflow-y-auto rounded-xl border border-gray-200/80 dark:border-gray-600/80 bg-gradient-to-b from-gray-50 to-white dark:from-gray-900/80 dark:to-gray-950 p-3 shadow-sm"
        >
          <div v-if="realLibrariesList.length" class="flex flex-wrap gap-2">
            <button
              v-for="lib in realLibrariesList"
              :key="lib.id"
              type="button"
              class="inline-flex max-w-full items-center rounded-full border px-3 py-1.5 text-sm font-medium transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500/40 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-gray-950"
              :class="
                isSourceLibSelected(lib.id)
                  ? 'border-primary-500/30 bg-primary-600 text-white shadow-sm hover:bg-primary-700 dark:bg-primary-600 dark:hover:bg-primary-500'
                  : 'border-gray-200 bg-white/90 text-gray-600 hover:border-primary-300/50 hover:bg-primary-50/80 hover:text-primary-800 dark:border-gray-600 dark:bg-gray-800/90 dark:text-gray-300 dark:hover:border-primary-500/40 dark:hover:bg-primary-950/40 dark:hover:text-primary-200'
              "
              @click="toggleSourceLibrary(lib.id)"
            >
              <span class="truncate">{{ lib.name }}</span>
            </button>
          </div>
          <p v-else class="py-6 text-center text-xs text-gray-400 dark:text-gray-500">无可用真实库</p>
        </div>
      </div>

      <!-- Cover section -->
      <div class="border-t border-gray-200 dark:border-gray-700 pt-4 mt-4">
        <h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">封面生成</h3>
        <div v-if="store.currentLibrary.image_tag" class="mb-3">
          <img :src="`/covers/${store.currentLibrary.id}.jpg?t=${store.currentLibrary.image_tag}`" class="h-24 rounded-lg object-cover" />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <BaseInput v-model="store.currentLibrary.cover_title_zh" label="封面中文标题" placeholder="留空使用库名称" />
          <BaseInput v-model="store.currentLibrary.cover_title_en" label="封面英文标题" placeholder="可选" />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
          <BaseSelect v-model="selectedStyle" label="封面样式">
            <option value="style_multi_1">样式一 (多图)</option>
            <option value="style_single_1">样式二 (单图)</option>
            <option value="style_single_2">样式三 (单图)</option>
          </BaseSelect>
          <BaseInput v-model="store.currentLibrary.cover_custom_image_path" label="自定义图片目录" placeholder="可选" />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
          <BaseInput v-model="store.currentLibrary.cover_custom_zh_font_path" label="自定义中文字体" placeholder="可选" />
          <BaseInput v-model="store.currentLibrary.cover_custom_en_font_path" label="自定义英文字体" placeholder="可选" />
        </div>

        <!-- Upload -->
        <div class="mt-3">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">上传素材图片</label>
          <input type="file" multiple accept="image/*" @change="handleFileUpload" class="block w-full text-sm text-gray-500 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-primary-50 file:text-primary-700 hover:file:bg-primary-100 dark:file:bg-primary-900/30 dark:file:text-primary-300" />
          <div v-if="uploadedPaths.length" class="flex flex-wrap gap-1 mt-2">
            <BaseTag v-for="(p, i) in uploadedPaths" :key="i" variant="info">图片 {{ i + 1 }}</BaseTag>
          </div>
        </div>

        <BaseButton class="mt-3" :loading="store.coverGenerating" @click="handleGenerateCover">
          {{ store.currentLibrary.image_tag ? '重新生成封面' : '生成封面' }}
        </BaseButton>
        <p class="text-xs text-gray-500 mt-1">需要先在客户端访问一次该虚拟库以生成缓存数据。</p>
      </div>
    </div>

    <template #footer>
      <BaseButton @click="store.dialogVisible = false">取消</BaseButton>
      <BaseButton variant="primary" :loading="store.saving" @click="store.saveLibrary()">
        {{ store.isEditing ? '保存' : '创建' }}
      </BaseButton>
    </template>
  </BaseDialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useMainStore } from '@/stores/main'
import api from '@/api'
import BaseDialog from '@/components/ui/BaseDialog.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseSearchMultiSelect from '@/components/ui/BaseSearchMultiSelect.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSwitch from '@/components/ui/BaseSwitch.vue'
import BaseTag from '@/components/ui/BaseTag.vue'

const store = useMainStore()
const resourceSearch = ref('')
const selectedStyle = ref('style_multi_1')
const uploadedPaths = ref([])

const realLibrariesList = computed(() => {
  const rlConfigs = store.config.real_libraries || []
  if (!rlConfigs.length) return (store.allLibrariesForSorting || []).filter(l => l.type === 'real')
  const enabledIds = new Set(rlConfigs.filter(r => r.enabled).map(r => r.id))
  return (store.allLibrariesForSorting || []).filter(l => l.type === 'real' && enabledIds.has(l.id))
})

function isSourceLibSelected(libId) {
  const arr = store.currentLibrary.source_libraries
  if (!Array.isArray(arr)) return false
  return arr.some((x) => String(x) === String(libId))
}

function toggleSourceLibrary(libId) {
  if (!Array.isArray(store.currentLibrary.source_libraries)) {
    store.currentLibrary.source_libraries = []
  }
  const arr = store.currentLibrary.source_libraries
  const i = arr.findIndex((x) => String(x) === String(libId))
  if (i >= 0) arr.splice(i, 1)
  else arr.push(libId)
}

const selectedResourceIdSet = computed(() =>
  new Set((store.currentLibrary.resource_ids || []).map((x) => String(x))),
)

const filteredResources = computed(() => {
  const type = store.currentLibrary.resource_type
  if (!type) return []
  if (type === 'person') {
    const merged = new Map()
    for (const p of personResults.value) {
      merged.set(String(p.id), p)
    }
    for (const id of selectedResourceIdSet.value) {
      if (!merged.has(id)) {
        const n = store.personNameCache[id]
        merged.set(id, { id, name: n && n !== '...' ? n : `ID:${id}` })
      }
    }
    let list = Array.from(merged.values())
    const q = resourceSearch.value.trim().toLowerCase()
    if (q) list = list.filter((p) => String(p.name || '').toLowerCase().includes(q))
    return list.slice(0, 100)
  }
  const keyMap = { collection: 'collections', tag: 'tags', genre: 'genres', studio: 'studios' }
  const all = store.classifications[keyMap[type]] || []
  if (!resourceSearch.value.trim()) return all.slice(0, 100)
  const q = resourceSearch.value.toLowerCase()
  return all.filter(i => i.name.toLowerCase().includes(q)).slice(0, 100)
})

const personResults = ref([])

function setResourceSearch(v) {
  resourceSearch.value = v
}

function resourceItemLabel(item) {
  return store.currentLibrary.resource_type === 'person'
    ? (store.personNameCache[item.id] || item.name)
    : item.name
}

function resourceChipLabel(id) {
  const sid = String(id)
  const type = store.currentLibrary.resource_type
  if (type === 'person') {
    return store.personNameCache[sid] && store.personNameCache[sid] !== '...'
      ? store.personNameCache[sid]
      : sid
  }
  const keyMap = { collection: 'collections', tag: 'tags', genre: 'genres', studio: 'studios' }
  const all = store.classifications[keyMap[type]] || []
  return all.find((i) => String(i.id) === sid)?.name || sid
}

function onResourceTypeChange() {
  store.currentLibrary.resource_id = ''
  store.currentLibrary.resource_ids = []
  resourceSearch.value = ''
  personResults.value = []
}

const onResourceSearch = async (query) => {
  if (store.currentLibrary.resource_type !== 'person') return
  const q = String(query ?? resourceSearch.value ?? '').trim()
  if (!q) {
    personResults.value = []
    return
  }
  try {
    const res = await api.searchPersons(q, 1)
    personResults.value = res.data || []
    personResults.value.forEach(p => { if (p.id && !store.personNameCache[p.id]) store.personNameCache[p.id] = p.name })
  } catch { personResults.value = [] }
}

const handleFileUpload = async (e) => {
  const files = e.target.files
  if (!files.length) return
  for (const file of files) {
    const formData = new FormData()
    formData.append('file', file)
    try {
      const res = await api.getConfig() // placeholder — use actual upload
      // Actually upload:
      const uploadRes = await fetch('/api/upload_temp_image', { method: 'POST', body: formData, headers: { Authorization: `Bearer ${localStorage.getItem('auth_token')}` } })
      const data = await uploadRes.json()
      if (data.path) uploadedPaths.value.push(data.path)
    } catch {}
  }
}

const handleGenerateCover = async () => {
  const titleZh = store.currentLibrary.cover_title_zh || store.currentLibrary.name
  const titleEn = store.currentLibrary.cover_title_en || ''
  await store.generateLibraryCover(store.currentLibrary.id, titleZh, titleEn, selectedStyle.value, uploadedPaths.value)
}

watch(() => store.dialogVisible, (val) => {
  if (!val) return
  selectedStyle.value = 'style_multi_1'
  uploadedPaths.value = []
  personResults.value = []
  const lib = store.currentLibrary
  const rt = lib.resource_type
  const ids = Array.isArray(lib.resource_ids) && lib.resource_ids.length
    ? lib.resource_ids
    : (lib.resource_id ? [lib.resource_id] : [])

  if (['all', 'rsshub', 'random'].includes(rt)) {
    resourceSearch.value = ''
    return
  }

  resourceSearch.value = ''

  if (rt === 'person' && ids.length) {
    for (const pid of ids) {
      const cached = store.personNameCache[pid]
      if (cached && cached !== '...') {
        personResults.value.push({ id: pid, name: cached })
      } else {
        store.resolvePersonName(pid)
        api.resolveItem(pid).then((r) => {
          if (!personResults.value.some((x) => String(x.id) === String(r.data.id))) {
            personResults.value.push(r.data)
          }
          store.personNameCache[r.data.id] = r.data.name
        }).catch(() => {})
      }
    }
  }
})
</script>
