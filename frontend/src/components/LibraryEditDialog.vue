<template>
  <BaseDialog
    :open="store.dialogVisible"
    :title="store.isEditing ? '编辑虚拟库' : '添加虚拟库'"
    size="lg"
    @close="store.dialogVisible = false"
    body-class="max-h-[70vh] overflow-y-auto"
  >
    <div class="space-y-4">
      <!-- Basic -->
      <BaseInput
        v-model="store.currentLibrary.name"
        label="虚拟库名称"
        placeholder="例如：豆瓣高分电影"
        required
      />

      <BaseSelect
        v-model="store.currentLibrary.resource_type"
        :options="resourceTypeOptions"
        label="资源类型"
        required
        @update:model-value="onResourceTypeChange"
      />

      <!-- RSS fields -->
      <template v-if="store.currentLibrary.resource_type === 'rsshub'">
        <BaseInput
          v-model="store.currentLibrary.rsshub_url"
          label="RSSHUB 链接"
          placeholder="https://rsshub.app/..."
          required
        />
        <BaseSelect
          v-model="store.currentLibrary.rss_type"
          :options="rssTypeOptions"
          label="RSS 类型"
          required
        />
        <div class="flex items-center gap-3">
          <label class="text-sm font-medium text-gray-700 dark:text-gray-300"
            >开启数据保留</label
          >
          <BaseSwitch v-model="store.currentLibrary.enable_retention" />
        </div>
        <div v-if="store.currentLibrary.enable_retention">
          <label
            class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
            >保留天数</label
          >
          <input
            type="number"
            v-model.number="store.currentLibrary.retention_days"
            min="0"
            class="block w-32 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 focus:outline-none"
          />
          <p class="mt-1 text-xs text-gray-500">0 表示永久保留。</p>
        </div>
        <BaseInput
          v-model="store.currentLibrary.fallback_tmdb_id"
          label="追加 TMDB ID"
          placeholder="可选"
        />
        <BaseSelect
          v-if="store.currentLibrary.fallback_tmdb_id"
          v-model="store.currentLibrary.fallback_tmdb_type"
          :options="fallbackTmdbTypeOptions"
          label="追加类型"
        />
      </template>

      <template v-if="store.currentLibrary.resource_type !== 'rsshub'">
        <BaseSelect
          v-model="store.currentLibrary.random_hide_rating_and_above"
          :options="randomRatingThresholdOptions"
          label="分级过滤"
          hint="隐藏所选分级及以上内容；留空表示不过滤。"
          placeholder="不过滤"
        />
      </template>

      <BaseSearchMultiSelect
        v-if="
          !['all', 'rsshub', 'random'].includes(
            store.currentLibrary.resource_type,
          )
        "
        v-model="store.currentLibrary.resource_ids"
        :search="resourceSearch"
        :options="filteredResources"
        :item-label="resourceItemLabel"
        :resolve-label="resourceChipLabel"
        :has-more="resourceHasMore"
        :loading-more="personLoadingMore"
        label="选择资源"
        required
        placeholder="搜索..."
        @update:search="setResourceSearch"
        @search="onResourceSearch"
        @load-more="onResourceLoadMore"
      />

      <!-- Refresh interval -->
      <div>
        <label
          class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
          >刷新间隔（小时）</label
        >
        <input
          type="number"
          v-model.number="store.currentLibrary.cache_refresh_interval"
          min="0"
          class="block w-32 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 focus:outline-none"
        />
        <p class="mt-1 text-xs text-gray-500">
          留空使用全局配置。0 表示仅手动刷新。
        </p>
      </div>

      <!-- Advanced filter -->
      <BaseSelect
        v-model="store.currentLibrary.advanced_filter_id"
        :options="advancedFilterOptions"
        label="高级筛选器"
        hint="留空表示不使用。"
        placeholder="无"
      />

      <!-- TMDB merge -->
      <div class="flex items-center gap-3">
        <label class="text-sm font-medium text-gray-700 dark:text-gray-300"
          >TMDB ID 合并</label
        >
        <BaseSwitch v-model="store.currentLibrary.merge_by_tmdb_id" />
        <span class="text-xs text-gray-500">同一 TMDB ID 只显示一个版本</span>
      </div>

      <!-- Source libraries -->
      <div v-if="store.currentLibrary.resource_type !== 'rsshub'">
        <label
          class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
          >源库范围</label
        >
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-2">
          点击标签切换选中；不选则搜索全部真实库。
        </p>
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
          <p
            v-else
            class="py-6 text-center text-xs text-gray-400 dark:text-gray-500"
          >
            无可用真实库
          </p>
        </div>
      </div>

      <!-- Cover section -->
      <div class="border-t border-gray-200 dark:border-gray-700 pt-4 mt-4">
        <h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">
          封面生成
        </h3>
        <div v-if="store.currentLibrary.image_tag" class="mb-3">
          <img
            :src="`/covers/${store.currentLibrary.id}.jpg?t=${store.currentLibrary.image_tag}`"
            class="h-24 rounded-lg object-cover"
          />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <BaseInput
            v-model="store.currentLibrary.cover_title_zh"
            label="封面中文标题"
            placeholder="留空使用库名称"
          />
          <BaseInput
            v-model="store.currentLibrary.cover_title_en"
            label="封面英文标题"
            placeholder="可选"
          />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
          <BaseSelect
            v-model="selectedStyle"
            :options="coverStyleOptions"
            label="封面样式"
          />
          <BaseInput
            v-model="store.currentLibrary.cover_custom_image_path"
            label="自定义图片目录"
            placeholder="可选"
          />
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
          <BaseInput
            v-model="store.currentLibrary.cover_custom_zh_font_path"
            label="自定义中文字体"
            placeholder="可选"
          />
          <BaseInput
            v-model="store.currentLibrary.cover_custom_en_font_path"
            label="自定义英文字体"
            placeholder="可选"
          />
        </div>

        <!-- Upload -->
        <div class="mt-3">
          <label
            class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
            >上传素材图片</label
          >
          <input
            type="file"
            multiple
            accept="image/*"
            @change="handleFileUpload"
            class="block w-full text-sm text-gray-500 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-primary-50 file:text-primary-700 hover:file:bg-primary-100 dark:file:bg-primary-900/30 dark:file:text-primary-300"
          />
          <div v-if="uploadedPaths.length" class="flex flex-wrap gap-1 mt-2">
            <BaseTag v-for="(p, i) in uploadedPaths" :key="i" variant="info"
              >图片 {{ i + 1 }}</BaseTag
            >
          </div>
        </div>

        <BaseButton
          class="mt-3"
          :loading="store.coverGenerating"
          @click="handleGenerateCover"
        >
          {{ store.currentLibrary.image_tag ? "重新生成封面" : "生成封面" }}
        </BaseButton>
        <p class="text-xs text-gray-500 mt-1">
          需要先在客户端访问一次该虚拟库以生成缓存数据。
        </p>
      </div>
    </div>

    <template #footer>
      <BaseButton @click="store.dialogVisible = false">取消</BaseButton>
      <BaseButton
        variant="primary"
        :loading="store.saving"
        @click="store.saveLibrary()"
      >
        {{ store.isEditing ? "保存" : "创建" }}
      </BaseButton>
    </template>
  </BaseDialog>
</template>

<script setup>
import { ref, computed, watch } from "vue";
import { useMainStore } from "@/stores/main";
import api from "@/api";
import BaseDialog from "@/components/ui/BaseDialog.vue";
import BaseInput from "@/components/ui/BaseInput.vue";
import BaseSelect from "@/components/ui/BaseSelect.vue";
import BaseSearchMultiSelect from "@/components/ui/BaseSearchMultiSelect.vue";
import BaseButton from "@/components/ui/BaseButton.vue";
import BaseSwitch from "@/components/ui/BaseSwitch.vue";
import BaseTag from "@/components/ui/BaseTag.vue";

/** 本地分类（合集/标签/类型/工作室）下拉分页大小，与人员接口每页 100 对齐量级 */
const LOCAL_RESOURCE_PAGE_SIZE = 80;

const store = useMainStore();
const resourceSearch = ref("");
const selectedStyle = ref("style_multi_1");
const uploadedPaths = ref([]);

const resourceTypeOptions = [
  { value: "all", label: "全库 (All)" },
  { value: "collection", label: "合集 (Collection)" },
  { value: "tag", label: "标签 (Tag)" },
  { value: "genre", label: "类型 (Genre)" },
  { value: "studio", label: "工作室 (Studio)" },
  { value: "person", label: "人员 (Person)" },
  { value: "rsshub", label: "RSSHUB" },
  { value: "random", label: "偏好推荐 (Random)" },
];
const rssTypeOptions = [
  { value: "douban", label: "豆瓣" },
  { value: "bangumi", label: "Bangumi" },
];
const fallbackTmdbTypeOptions = [
  { value: "Movie", label: "电影" },
  { value: "TV", label: "电视剧" },
];
const coverStyleOptions = [
  { value: "style_multi_1", label: "样式一 (多图)" },
  { value: "style_single_1", label: "样式二 (单图)" },
  { value: "style_single_2", label: "样式三 (单图)" },
  { value: "style_shelf_1", label: "样式四 (背景+底栏海报)" },
];
const advancedFilterOptions = computed(() => [
  { value: null, label: "无" },
  ...(store.config.advanced_filters || []).map((f) => ({
    value: f.id,
    label: f.name,
  })),
]);

const randomRatingThresholdOptions = computed(() => {
  const src = Array.isArray(store.classifications?.official_ratings)
    ? store.classifications.official_ratings
    : [];
  const seen = new Set();
  const items = [];

  for (const x of src) {
    const value = String(x?.id ?? x?.name ?? x ?? "").trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    items.push({ value, label: value });
  }

  return [{ value: null, label: "不过滤" }, ...items];
});

const realLibrariesList = computed(() => {
  const rlConfigs = store.config.real_libraries || [];
  if (!rlConfigs.length)
    return (store.allLibrariesForSorting || []).filter(
      (l) => l.type === "real",
    );
  const enabledIds = new Set(
    rlConfigs.filter((r) => r.enabled).map((r) => r.id),
  );
  return (store.allLibrariesForSorting || []).filter(
    (l) => l.type === "real" && enabledIds.has(l.id),
  );
});

function isSourceLibSelected(libId) {
  const arr = store.currentLibrary.source_libraries;
  if (!Array.isArray(arr)) return false;
  return arr.some((x) => String(x) === String(libId));
}

function toggleSourceLibrary(libId) {
  if (!Array.isArray(store.currentLibrary.source_libraries)) {
    store.currentLibrary.source_libraries = [];
  }
  const arr = store.currentLibrary.source_libraries;
  const i = arr.findIndex((x) => String(x) === String(libId));
  if (i >= 0) arr.splice(i, 1);
  else arr.push(libId);
}

const selectedResourceIdSet = computed(
  () =>
    new Set((store.currentLibrary.resource_ids || []).map((x) => String(x))),
);

const localResourceVisibleEnd = ref(LOCAL_RESOURCE_PAGE_SIZE);
const personPage = ref(1);
const personHasMore = ref(false);
const personLoadingMore = ref(false);

const classificationKeyMap = {
  collection: "collections",
  tag: "tags",
  genre: "genres",
  studio: "studios",
};

/** 当前类型下、按搜索词过滤后的完整列表（数据仍来自已加载的 classifications） */
const classificationFilteredList = computed(() => {
  const type = store.currentLibrary.resource_type;
  if (!classificationKeyMap[type]) return [];
  const all = store.classifications[classificationKeyMap[type]] || [];
  const q = resourceSearch.value.trim().toLowerCase();
  if (!q) return all;
  return all.filter((i) => i.name.toLowerCase().includes(q));
});

const filteredResources = computed(() => {
  const type = store.currentLibrary.resource_type;
  if (!type) return [];
  if (type === "person") {
    const merged = new Map();
    for (const p of personResults.value) {
      merged.set(String(p.id), p);
    }
    for (const id of selectedResourceIdSet.value) {
      if (!merged.has(id)) {
        const n = store.personNameCache[id];
        merged.set(id, { id, name: n && n !== "..." ? n : `ID:${id}` });
      }
    }
    let list = Array.from(merged.values());
    const q = resourceSearch.value.trim().toLowerCase();
    if (q) {
      list = list.filter((p) =>
        String(p.name || "")
          .toLowerCase()
          .includes(q),
      );
    }
    return list;
  }
  if (!classificationKeyMap[type]) return [];
  const slice = classificationFilteredList.value.slice(
    0,
    localResourceVisibleEnd.value,
  );
  const merged = new Map();
  for (const i of slice) {
    merged.set(String(i.id), i);
  }
  const allRaw =
    store.classifications[classificationKeyMap[type]] || [];
  for (const id of selectedResourceIdSet.value) {
    if (!merged.has(id)) {
      const found = allRaw.find((x) => String(x.id) === id);
      if (found) merged.set(id, found);
    }
  }
  return Array.from(merged.values());
});

const resourceHasMore = computed(() => {
  const type = store.currentLibrary.resource_type;
  if (type === "person") return personHasMore.value;
  if (classificationKeyMap[type]) {
    return (
      localResourceVisibleEnd.value < classificationFilteredList.value.length
    );
  }
  return false;
});

const personResults = ref([]);
let personFetchSeq = 0;

function resetClassificationPaging() {
  localResourceVisibleEnd.value = LOCAL_RESOURCE_PAGE_SIZE;
}

function resetPersonPaging() {
  personPage.value = 1;
  personHasMore.value = false;
  personLoadingMore.value = false;
}

function applyPersonResults(data) {
  personResults.value = data || [];
  personResults.value.forEach((p) => {
    if (p.id && !store.personNameCache[p.id])
      store.personNameCache[p.id] = p.name;
  });
}

const PERSON_PAGE_SIZE = 100;

/** 有关键词走 Items 搜索；无关键词走 /Persons 分页（与后端约定一致） */
async function fetchPersonListForQuery(q) {
  const seq = ++personFetchSeq;
  resetPersonPaging();
  const trimmed = String(q ?? "").trim();
  try {
    const res = trimmed
      ? await api.searchPersons(trimmed, 1)
      : await api.searchPersons(undefined, 1);
    if (seq !== personFetchSeq) return;
    applyPersonResults(res.data);
    const batch = res.data || [];
    personHasMore.value = batch.length >= PERSON_PAGE_SIZE;
  } catch {
    if (seq !== personFetchSeq) return;
    personResults.value = [];
    personHasMore.value = false;
  }
}

async function fetchPersonNextPage() {
  if (!personHasMore.value || personLoadingMore.value) return;
  const listGen = personFetchSeq;
  personLoadingMore.value = true;
  const nextPage = personPage.value + 1;
  const trimmed = resourceSearch.value.trim();
  try {
    const res = trimmed
      ? await api.searchPersons(trimmed, nextPage)
      : await api.searchPersons(undefined, nextPage);
    if (listGen !== personFetchSeq) return;
    const batch = res.data || [];
    if (batch.length < PERSON_PAGE_SIZE) personHasMore.value = false;
    const map = new Map(
      personResults.value.map((p) => [String(p.id), p]),
    );
    for (const p of batch) {
      map.set(String(p.id), p);
      if (p.id && !store.personNameCache[p.id])
        store.personNameCache[p.id] = p.name;
    }
    personResults.value = Array.from(map.values());
    personPage.value = nextPage;
  } catch {
    if (listGen === personFetchSeq) personHasMore.value = false;
  } finally {
    personLoadingMore.value = false;
  }
}

function onResourceLoadMore() {
  const rt = store.currentLibrary.resource_type;
  if (rt === "person") {
    void fetchPersonNextPage();
    return;
  }
  if (classificationKeyMap[rt]) {
    const full = classificationFilteredList.value.length;
    if (localResourceVisibleEnd.value >= full) return;
    localResourceVisibleEnd.value = Math.min(
      localResourceVisibleEnd.value + LOCAL_RESOURCE_PAGE_SIZE,
      full,
    );
  }
}

function setResourceSearch(v) {
  resourceSearch.value = v;
}

function resourceItemLabel(item) {
  return store.currentLibrary.resource_type === "person"
    ? store.personNameCache[item.id] || item.name
    : item.name;
}

function resourceChipLabel(id) {
  const sid = String(id);
  const type = store.currentLibrary.resource_type;
  if (type === "person") {
    return store.personNameCache[sid] && store.personNameCache[sid] !== "..."
      ? store.personNameCache[sid]
      : sid;
  }
  const keyMap = {
    collection: "collections",
    tag: "tags",
    genre: "genres",
    studio: "studios",
  };
  const all = store.classifications[keyMap[type]] || [];
  return all.find((i) => String(i.id) === sid)?.name || sid;
}

function onResourceTypeChange() {
  store.currentLibrary.resource_id = "";
  store.currentLibrary.resource_ids = [];
  resourceSearch.value = "";
  personResults.value = [];
  resetClassificationPaging();
  resetPersonPaging();
  if (store.currentLibrary.resource_type === "person") {
    void fetchPersonListForQuery("");
  }
}

const onResourceSearch = async (query) => {
  const rt = store.currentLibrary.resource_type;
  if (rt === "person") {
    const q = String(query ?? resourceSearch.value ?? "");
    await fetchPersonListForQuery(q);
  }
};

const handleFileUpload = async (e) => {
  const files = e.target.files;
  if (!files.length) return;
  for (const file of files) {
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await api.getConfig(); // placeholder — use actual upload
      // Actually upload:
      const uploadRes = await fetch("/api/upload_temp_image", {
        method: "POST",
        body: formData,
        headers: {
          Authorization: `Bearer ${localStorage.getItem("auth_token")}`,
        },
      });
      const data = await uploadRes.json();
      if (data.path) uploadedPaths.value.push(data.path);
    } catch {}
  }
};

const handleGenerateCover = async () => {
  const titleZh =
    store.currentLibrary.cover_title_zh || store.currentLibrary.name;
  const titleEn = store.currentLibrary.cover_title_en || "";
  await store.generateLibraryCover(
    store.currentLibrary.id,
    titleZh,
    titleEn,
    selectedStyle.value,
    uploadedPaths.value,
  );
};

watch(resourceSearch, () => {
  const rt = store.currentLibrary.resource_type;
  if (classificationKeyMap[rt]) {
    resetClassificationPaging();
  }
});

watch(
  () => store.dialogVisible,
  (val) => {
    if (!val) return;
    selectedStyle.value = "style_multi_1";
    uploadedPaths.value = [];
    personResults.value = [];
    resetClassificationPaging();
    resetPersonPaging();
    const lib = store.currentLibrary;
    const rt = lib.resource_type;
    const ids =
      Array.isArray(lib.resource_ids) && lib.resource_ids.length
        ? lib.resource_ids
        : lib.resource_id
          ? [lib.resource_id]
          : [];

    if (["all", "rsshub", "random"].includes(rt)) {
      resourceSearch.value = "";
      return;
    }

    resourceSearch.value = "";

    if (rt === "person") {
      for (const pid of ids) {
        if (
          !store.personNameCache[pid] ||
          store.personNameCache[pid] === "..."
        ) {
          store.resolvePersonName(pid);
          api
            .resolveItem(pid)
            .then((r) => {
              store.personNameCache[r.data.id] = r.data.name;
            })
            .catch(() => {});
        }
      }
      void fetchPersonListForQuery("");
    }
  },
);
</script>
