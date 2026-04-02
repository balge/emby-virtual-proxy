<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-2xl font-bold text-gray-900 dark:text-gray-100">高级筛选器</h1>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">创建和管理筛选规则</p>
      </div>
      <div class="flex gap-2">
        <BaseButton @click="helpVisible = true">性能指南</BaseButton>
        <BaseButton variant="primary" @click="openAdd">新增筛选器</BaseButton>
      </div>
    </div>

    <!-- Empty -->
    <div v-if="!filters.length" class="text-center py-20">
      <FunnelIcon class="w-12 h-12 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
      <p class="text-gray-500 dark:text-gray-400">暂无筛选器</p>
    </div>

    <!-- List -->
    <div v-else class="space-y-3">
      <div v-for="f in filters" :key="f.id" class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4 flex items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="flex items-center gap-2 mb-1">
            <span class="font-medium text-gray-900 dark:text-gray-100 text-sm">{{ f.name }}</span>
            <BaseTag :variant="f.match_all ? 'success' : 'warning'">{{ f.match_all ? 'AND' : 'OR' }}</BaseTag>
            <BaseTag v-if="f.sort_field" variant="info">排序: {{ getSortLabel(f.sort_field) }}</BaseTag>
          </div>
          <p class="text-xs text-gray-500">{{ f.rules.length }} 条规则</p>
        </div>
        <div class="flex gap-1.5 shrink-0">
          <BaseButton size="sm" @click="openEdit(f)">编辑</BaseButton>
          <ConfirmPopover message="确定删除这个筛选器？" @confirm="deleteFilter(f.id)">
            <template #trigger><BaseButton size="sm" variant="danger-outline">删除</BaseButton></template>
          </ConfirmPopover>
        </div>
      </div>
    </div>

    <!-- Edit dialog -->
    <BaseDialog :open="editVisible" :title="isEditing ? '编辑筛选器' : '新增筛选器'" size="xl" @close="editVisible = false" body-class="max-h-[70vh] overflow-y-auto">
      <div class="space-y-4">
        <BaseInput v-model="current.name" label="筛选器名称" required />
        <div class="flex items-center gap-4">
          <label class="text-sm font-medium text-gray-700 dark:text-gray-300">匹配逻辑</label>
          <label class="flex items-center gap-1.5 text-sm cursor-pointer">
            <input type="radio" :value="true" v-model="current.match_all" class="text-primary-600 focus:ring-primary-500" /> 所有 (AND)
          </label>
          <label class="flex items-center gap-1.5 text-sm cursor-pointer">
            <input type="radio" :value="false" v-model="current.match_all" class="text-primary-600 focus:ring-primary-500" /> 任意 (OR)
          </label>
        </div>

        <!-- Sort -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <BaseSelect v-model="current.sort_field" label="排序字段" hint="留空使用 Emby 原生排序。">
            <option :value="null">Emby 原生</option>
            <option v-for="s in sortFields" :key="s.value" :value="s.value">{{ s.label }}</option>
          </BaseSelect>
          <BaseSelect v-model="current.sort_order" label="排序方向">
            <option :value="null">Emby 原生</option>
            <option value="desc">倒序（从大到小）</option>
            <option value="asc">正序（从小到大）</option>
          </BaseSelect>
        </div>

        <!-- Rules -->
        <div class="border-t border-gray-200 dark:border-gray-700 pt-4">
          <h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">规则</h3>
          <div v-for="(rule, idx) in current.rules" :key="idx" class="flex flex-wrap items-center gap-2 mb-3 p-3 bg-gray-50 dark:bg-gray-700/30 rounded-lg">
            <select v-model="rule.field" class="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500/20 w-48">
              <option v-for="f in ruleFields" :key="f.value" :value="f.value">{{ f.label }}</option>
            </select>
            <select v-model="rule.operator" class="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500/20 w-28">
              <option v-for="o in operators" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
            <template v-if="!['is_empty', 'is_not_empty'].includes(rule.operator)">
              <template v-if="['PremiereDate', 'DateCreated', 'DateLastMediaAdded'].includes(rule.field)">
                <input type="date" v-model="rule.value" :disabled="!!rule.relative_days" class="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500/20" />
                <input type="number" :value="rule.relative_days" @input="setRelDays(rule, $event.target.value)" placeholder="最近N天" min="1" class="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm w-24 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500/20" />
              </template>
              <select v-else-if="rule.field === 'ProductionLocations'" v-model="rule.value" class="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500/20 w-36">
                <option v-for="c in countries" :key="c.code" :value="c.code">{{ c.name }} ({{ c.code }})</option>
              </select>
              <input v-else v-model="rule.value" placeholder="值" class="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500/20 w-32" />
            </template>
            <button @click="current.rules.splice(idx, 1)" class="p-1 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors">
              <XMarkIcon class="w-4 h-4" />
            </button>
          </div>
          <BaseButton size="sm" @click="current.rules.push({ field: '', operator: 'equals', value: '', relative_days: null })">添加规则</BaseButton>
        </div>
      </div>

      <template #footer>
        <BaseButton @click="editVisible = false">取消</BaseButton>
        <BaseButton variant="primary" :loading="store.saving" @click="saveFilter">保存</BaseButton>
      </template>
    </BaseDialog>

    <!-- Help dialog -->
    <BaseDialog :open="helpVisible" title="高级筛选器性能指南" size="xl" @close="helpVisible = false">
      <div class="prose prose-sm dark:prose-invert max-w-none">
        <h4 class="text-emerald-600">🚀 高效规则</h4>
        <p>当规则的字段和操作符组合被 Emby 原生支持时，筛选由服务器执行，速度最快。</p>
        <ul>
          <li>评分类（社区评分、影评人评分）：大于、小于、等于</li>
          <li>日期类（年份、首播日期、添加日期）：大于、小于、等于</li>
          <li>分类类（类型、标签、工作室等）：等于</li>
          <li>布尔类（是否电影、已播放等）：等于</li>
          <li>存在性（TMDB/IMDB ID）：为空、不为空</li>
        </ul>
        <h4 class="text-amber-600">🐢 低效规则</h4>
        <p>以下情况会降级到代理端处理，可能影响性能：</p>
        <ul>
          <li>匹配逻辑为 OR</li>
          <li>字段为「名称」或「地区」</li>
          <li>操作符为「不等于」「包含」「不包含」</li>
        </ul>
      </div>
      <template #footer>
        <BaseButton variant="primary" @click="helpVisible = false">我明白了</BaseButton>
      </template>
    </BaseDialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { FunnelIcon, XMarkIcon } from '@heroicons/vue/24/outline'
import { useMainStore } from '@/stores/main'
import { useToast } from '@/composables/useToast'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseTag from '@/components/ui/BaseTag.vue'
import BaseDialog from '@/components/ui/BaseDialog.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import ConfirmPopover from '@/components/ui/ConfirmPopover.vue'

const store = useMainStore()
const toast = useToast()
const filters = computed(() => store.config.advanced_filters || [])

const editVisible = ref(false)
const helpVisible = ref(false)
const isEditing = ref(false)
const current = ref({})

const sortFields = [
  { value: 'CommunityRating', label: '社区评分' }, { value: 'CriticRating', label: '影评人评分' },
  { value: 'ProductionYear', label: '发行年份' }, { value: 'PremiereDate', label: '首播日期' },
  { value: 'DateCreated', label: '添加日期' }, { value: 'DateLastMediaAdded', label: '最近入库' },
  { value: 'SortName', label: '名称' },
]
const getSortLabel = (f) => sortFields.find(s => s.value === f)?.label || f

const ruleFields = [
  { value: 'CommunityRating', label: '社区评分' }, { value: 'CriticRating', label: '影评人评分' },
  { value: 'OfficialRating', label: '官方分级' }, { value: 'ProductionYear', label: '发行年份' },
  { value: 'PremiereDate', label: '首播日期' }, { value: 'DateCreated', label: '添加日期' },
  { value: 'DateLastMediaAdded', label: '最近入库' }, { value: 'Genres', label: '类型' },
  { value: 'Tags', label: '标签' }, { value: 'Studios', label: '工作室' },
  { value: 'VideoRange', label: '视频范围' }, { value: 'Container', label: '文件容器' },
  { value: 'NameStartsWith', label: '名称开头' }, { value: 'SeriesStatus', label: '剧集状态' },
  { value: 'IsMovie', label: '是否电影' }, { value: 'IsSeries', label: '是否剧集' },
  { value: 'IsPlayed', label: '已播放' }, { value: 'IsUnplayed', label: '未播放' },
  { value: 'HasSubtitles', label: '有字幕' }, { value: 'HasOfficialRating', label: '有官方评级' },
  { value: 'ProviderIds.Tmdb', label: '有TMDB ID' }, { value: 'ProviderIds.Imdb', label: '有IMDB ID' },
  { value: 'ProductionLocations', label: '地区' }, { value: 'Name', label: '名称' },
]

const operators = [
  { value: 'equals', label: '等于' }, { value: 'not_equals', label: '不等于' },
  { value: 'contains', label: '包含' }, { value: 'not_contains', label: '不包含' },
  { value: 'greater_than', label: '大于' }, { value: 'less_than', label: '小于' },
  { value: 'is_empty', label: '为空' }, { value: 'is_not_empty', label: '不为空' },
]

const countries = [
  { code: 'CN', name: '中国内地' }, { code: 'HK', name: '香港' }, { code: 'TW', name: '台湾' },
  { code: 'JP', name: '日本' }, { code: 'KR', name: '韩国' }, { code: 'US', name: '美国' },
  { code: 'GB', name: '英国' }, { code: 'FR', name: '法国' }, { code: 'DE', name: '德国' },
  { code: 'IT', name: '意大利' }, { code: 'ES', name: '西班牙' }, { code: 'RU', name: '俄罗斯' },
  { code: 'IN', name: '印度' }, { code: 'TH', name: '泰国' }, { code: 'AU', name: '澳大利亚' },
  { code: 'CA', name: '加拿大' }, { code: 'BR', name: '巴西' }, { code: 'SE', name: '瑞典' },
  { code: 'NL', name: '荷兰' }, { code: 'TR', name: '土耳其' },
]

const setRelDays = (rule, val) => {
  const n = parseInt(val)
  if (n > 0) { rule.relative_days = n; rule.value = null; rule.operator = 'greater_than' }
  else { rule.relative_days = null }
}

const openAdd = () => {
  isEditing.value = false
  current.value = { id: crypto.randomUUID(), name: '', match_all: true, rules: [], sort_field: null, sort_order: null }
  editVisible.value = true
}

const openEdit = (f) => {
  isEditing.value = true
  current.value = JSON.parse(JSON.stringify(f))
  editVisible.value = true
}

const saveFilter = async () => {
  if (!current.value.name || !current.value.rules.length) { toast.warning('请填写名称并至少添加一条规则'); return }
  const newFilters = [...(store.config.advanced_filters || [])]
  if (isEditing.value) {
    const idx = newFilters.findIndex(f => f.id === current.value.id)
    if (idx !== -1) newFilters[idx] = current.value
  } else {
    newFilters.push(current.value)
  }
  try { await store.saveAdvancedFilters(newFilters); editVisible.value = false; toast.success('筛选器已保存') } catch {}
}

const deleteFilter = async (id) => {
  const newFilters = (store.config.advanced_filters || []).filter(f => f.id !== id)
  try { await store.saveAdvancedFilters(newFilters); toast.success('筛选器已删除') } catch {}
}

onMounted(() => { if (!store.dataStatus) store.fetchAllInitialData() })
</script>
