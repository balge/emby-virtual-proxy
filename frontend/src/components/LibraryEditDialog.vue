<template>
  <el-dialog
    :model-value="store.dialogVisible"
    :title="store.isEditing ? '编辑虚拟库' : '添加虚拟库'"
    width="600px"
    @close="store.dialogVisible = false"
    :close-on-click-modal="false"
  >
    <el-form :model="store.currentLibrary" label-width="120px" v-loading="store.saving" class="lib-form">
      <el-form-item label="虚拟库名称" required>
        <el-input v-model="store.currentLibrary.name" placeholder="例如：豆瓣高分电影"></el-input>
      </el-form-item>
      
      <el-form-item label="资源类型" required>
        <el-select v-model="store.currentLibrary.resource_type" @change="store.currentLibrary.resource_id = ''">
          <el-option label="全库 (All Libraries)" value="all"></el-option>
          <el-option label="合集 (Collection)" value="collection"></el-option>
          <el-option label="标签 (Tag)" value="tag"></el-option>
          <el-option label="类型 (Genre)" value="genre"></el-option>
          <el-option label="工作室 (Studio)" value="studio"></el-option>
          <el-option label="人员 (Person)" value="person"></el-option>
          <el-option label="RSSHUB" value="rsshub"></el-option>
          <el-option label="偏好推荐 (Random)" value="random"></el-option>
        </el-select>
      </el-form-item>
      
      <el-form-item label="RSSHUB 链接" required v-if="store.currentLibrary.resource_type === 'rsshub'">
        <el-input v-model="store.currentLibrary.rsshub_url" placeholder="请输入 RSSHUB 链接"></el-input>
      </el-form-item>

      <el-form-item label="RSS 类型" required v-if="store.currentLibrary.resource_type === 'rsshub'">
        <el-select v-model="store.currentLibrary.rss_type" placeholder="请选择 RSS 类型">
          <el-option label="豆瓣" value="douban"></el-option>
          <el-option label="Bangumi" value="bangumi"></el-option>
        </el-select>
      </el-form-item>

      <el-form-item label="刷新间隔(小时)">
        <el-input-number
          v-model="store.currentLibrary.cache_refresh_interval"
          :min="0"
          :step="1"
          :controls-position="'right'"
          placeholder="留空使用全局配置"
          style="width: 220px;"
        />
        <el-tooltip content="统一刷新间隔。RSS 定时调度和虚拟库缓存 TTL 都使用它。留空时使用系统设置中的全局值；0 表示仅在刷新事件时失效（RSS 定时不触发）。" placement="top">
          <el-icon style="margin-left: 8px; color: #aaa;"><InfoFilled /></el-icon>
        </el-tooltip>
      </el-form-item>

      <el-form-item label="开启数据保留" v-if="store.currentLibrary.resource_type === 'rsshub'">
        <el-switch v-model="store.currentLibrary.enable_retention"></el-switch>
        <el-tooltip content="开启后，即使条目从 RSS 源中消失，也会在本地保留一段时间。关闭则每次刷新都会清空重建（与 RSS 源完全同步）。" placement="top">
           <el-icon style="margin-left: 8px; color: #aaa;"><InfoFilled /></el-icon>
        </el-tooltip>
      </el-form-item>

      <el-form-item label="保留天数" v-if="store.currentLibrary.resource_type === 'rsshub' && store.currentLibrary.enable_retention">
        <el-input-number v-model="store.currentLibrary.retention_days" :min="0" :step="1"></el-input-number>
        <el-tooltip content="条目首次被添加到库中后，在库中保留的最长天数。0 表示永久保留（不自动清理）。" placement="top">
          <el-icon style="margin-left: 8px; color: #aaa;"><InfoFilled /></el-icon>
        </el-tooltip>
      </el-form-item>

      <el-form-item label="追加 TMDB ID" v-if="store.currentLibrary.resource_type === 'rsshub'">
        <el-input v-model="store.currentLibrary.fallback_tmdb_id" placeholder="可选，额外追加一个指定的影视项目"></el-input>
      </el-form-item>

      <el-form-item label="追加类型" v-if="store.currentLibrary.resource_type === 'rsshub' && store.currentLibrary.fallback_tmdb_id">
        <el-select v-model="store.currentLibrary.fallback_tmdb_type" placeholder="请选择要追加的 TMDB 项目类型">
          <el-option label="电影 (Movie)" value="Movie"></el-option>
          <el-option label="电视剧 (TV)" value="TV"></el-option>
        </el-select>
      </el-form-item>

      <el-form-item label="选择资源" required v-if="store.currentLibrary.resource_type !== 'all' && store.currentLibrary.resource_type !== 'rsshub' && store.currentLibrary.resource_type !== 'random'">
        <el-select 
          v-model="store.currentLibrary.resource_id"
          filterable
          remote
          :remote-method="searchResource"
          :loading="false" 
          placeholder="请输入关键词搜索"
          style="width: 100%;"
          popper-class="resource-select-popper"
        >
          <el-option
            v-for="item in availableResources"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          >
            <span v-if="store.currentLibrary.resource_type === 'person'">{{ store.personNameCache[item.id] || item.name }}</span>
            <span v-else>{{ item.name }}</span>
          </el-option>
          <div v-if="resourceLoading" class="loading-indicator">加载中...</div>
        </el-select>
      </el-form-item>

      <el-form-item label="高级筛选器">
        <el-select 
          v-model="store.currentLibrary.advanced_filter_id" 
          placeholder="可不选，留空表示不使用"
          style="width: 100%;"
          clearable  
        >
          <el-option label="无" :value="null" /> 
          <el-option
            v-for="filter in store.config.advanced_filters"
            :key="filter.id"
            :label="filter.name"
            :value="filter.id"
          />
        </el-select>
      </el-form-item>
      
      <el-form-item label="TMDB ID 合并">
        <el-switch v-model="store.currentLibrary.merge_by_tmdb_id"></el-switch>
        <el-tooltip content="开启后，同一TMDB ID的影视项目将只显示一个版本，通常用于整合4K和1080p版本。" placement="top">
          <el-icon style="margin-left: 8px; color: #aaa;"><InfoFilled /></el-icon>
        </el-tooltip>
      </el-form-item>

      <el-form-item label="源库范围" v-if="store.currentLibrary.resource_type !== 'rsshub'">
        <el-select
          v-model="store.currentLibrary.source_libraries"
          multiple
          filterable
          collapse-tags
          collapse-tags-tooltip
          placeholder="不选则搜索全部真实库"
          style="width: 100%;"
          clearable
        >
          <el-option
            v-for="lib in realLibrariesList"
            :key="lib.id"
            :label="lib.name"
            :value="lib.id"
          />
        </el-select>
        <el-tooltip content="限定虚拟库的数据来源。留空表示搜索所有真实库。" placement="top">
          <el-icon style="margin-left: 8px; color: #aaa;"><InfoFilled /></el-icon>
        </el-tooltip>
      </el-form-item>
      
      <el-divider>封面生成</el-divider>

      <el-form-item label="当前封面">
        <div class="cover-preview-wrapper">
          <img v-if="store.currentLibrary.image_tag" :src="coverImageUrl" class="cover-preview-image" />
          <div v-else class="cover-preview-placeholder">暂无封面</div>
        </div>
      </el-form-item>
      
      <el-form-item label="封面中文标题">
         <el-input v-model="store.currentLibrary.cover_title_zh" placeholder="可选，留空则使用虚拟库名称"></el-input>
      </el-form-item>

      <el-form-item label="封面英文标题">
         <el-input v-model="store.currentLibrary.cover_title_en" placeholder="可选，用于封面上的英文装饰文字"></el-input>
      </el-form-item>

      <el-form-item label="封面样式">
        <el-select v-model="selectedStyle" placeholder="请选择样式">
          <el-option label="样式一 (多图)" value="style_multi_1"></el-option>
          <el-option label="样式二 (单图)" value="style_single_1"></el-option>
          <el-option label="样式三 (单图)" value="style_single_2"></el-option>
        </el-select>
      </el-form-item>

      <el-form-item label="自定义中文字体">
        <el-input v-model="store.currentLibrary.cover_custom_zh_font_path" placeholder="可选，留空则使用全局字体"></el-input>
      </el-form-item>

      <el-form-item label="自定义英文字体">
        <el-input v-model="store.currentLibrary.cover_custom_en_font_path" placeholder="可选，留空则使用全局字体"></el-input>
      </el-form-item>

      <el-form-item label="自定义图片目录">
        <el-input v-model="store.currentLibrary.cover_custom_image_path" placeholder="可选，留空则从虚拟库下载封面"></el-input>
      </el-form-item>

      <el-form-item label="上传素材图片">
        <el-upload
          action="/api/upload_temp_image"
          list-type="picture-card"
          :on-success="handleUploadSuccess"
          :on-remove="handleRemove"
          :file-list="uploadedFiles"
          :limit="9"
          multiple
        >
          <el-icon><Plus /></el-icon>
        </el-upload>
      </el-form-item>

      <el-form-item>
         <el-button
            type="primary"
            @click="handleGenerateCover" 
            :loading="store.coverGenerating"
          >
            {{ store.currentLibrary.image_tag ? '重新生成封面' : '生成封面' }}
          </el-button>
          <div class="button-tips">
            <p class="tip">此功能将从该虚拟库中随机选取内容自动合成封面图。</p>
            <p class="tip tip-warning">注意：生成封面需要缓存数据。请先在客户端访问一次该虚拟库，然后再点此生成。</p>
          </div>
      </el-form-item>

    </el-form>
    <template #footer>
      <span class="dialog-footer">
        <el-button @click="store.dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="store.saveLibrary()" :loading="store.saving">
          {{ store.isEditing ? '保存' : '创建' }}
        </el-button>
      </span>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useMainStore } from '@/stores/main';
import { InfoFilled, Plus } from '@element-plus/icons-vue';
import api from '@/api';

const store = useMainStore();
const resourceLoading = ref(false);

const realLibrariesList = computed(() => {
  const realLibConfigs = store.config.real_libraries || [];
  if (realLibConfigs.length === 0) {
    // 尚未同步真实库配置，显示所有真实库
    return (store.allLibrariesForSorting || []).filter(lib => lib.type === 'real');
  }
  const enabledIds = new Set(realLibConfigs.filter(rl => rl.enabled).map(rl => rl.id));
  return (store.allLibrariesForSorting || []).filter(lib => lib.type === 'real' && enabledIds.has(lib.id));
});
const availableResources = ref([]);
const currentQuery = ref('');
const page = ref(1);
const hasMore = ref(true);
const selectedStyle = ref('style_multi_1');
const uploadedFiles = ref([]);

const coverImageUrl = computed(() => {
  if (store.currentLibrary?.image_tag) {
    return `/covers/${store.currentLibrary.id}.jpg?t=${store.currentLibrary.image_tag}`;
  }
  return '';
});

const searchResource = async (query) => {
  currentQuery.value = query;
  page.value = 1;
  availableResources.value = [];
  hasMore.value = true;
  await loadMore();
};

const loadMore = async () => {
  if (!store.currentLibrary.resource_type || !hasMore.value) return;
  
  resourceLoading.value = true;
  try {
    if (store.currentLibrary.resource_type === 'person') {
      const response = await api.searchPersons(currentQuery.value, page.value);
      if (response.data && response.data.length > 0) {
        availableResources.value.push(...response.data);
        response.data.forEach(person => {
            if (person.id && !store.personNameCache[person.id]) {
                store.personNameCache[person.id] = person.name;
            }
        });
        page.value++;
        hasMore.value = response.data.length === 100;
      } else {
        hasMore.value = false;
      }
    } else {
      const resourceKeyMap = {
        collection: 'collections',
        tag: 'tags',
        genre: 'genres',
        studio: 'studios',
      };
      const key = resourceKeyMap[store.currentLibrary.resource_type];
      const allItems = store.classifications[key] || [];
      
      let filteredItems = allItems;
      if (currentQuery.value) {
        filteredItems = allItems.filter(item =>
          item.name.toLowerCase().includes(currentQuery.value.toLowerCase())
        );
      }
      
      const currentLength = availableResources.value.length;
      const nextItems = filteredItems.slice(currentLength, currentLength + 100);
      
      if (nextItems.length > 0) {
        availableResources.value.push(...nextItems);
      }
      
      if (availableResources.value.length >= filteredItems.length) {
        hasMore.value = false;
      }
    }
  } catch (error) {
    console.error("加载资源失败:", error);
    hasMore.value = false;
  } finally {
    resourceLoading.value = false;
  }
};

const handleGenerateCover = async () => {
    const titleZh = store.currentLibrary.cover_title_zh || store.currentLibrary.name;
    const titleEn = store.currentLibrary.cover_title_en || '';
    const tempImagePaths = uploadedFiles.value.map(file => file.response.path);
    const success = await store.generateLibraryCover(store.currentLibrary.id, titleZh, titleEn, selectedStyle.value, tempImagePaths);
}

const handleUploadSuccess = (response, file, fileList) => {
  uploadedFiles.value = fileList;
};

const handleRemove = (file, fileList) => {
  uploadedFiles.value = fileList;
};

let scrollWrapper = null;

const handleScroll = (event) => {
  const { scrollTop, clientHeight, scrollHeight } = event.target;
  if (scrollHeight - scrollTop <= clientHeight + 10) {
    if (!resourceLoading.value && hasMore.value) {
      loadMore();
    }
  }
};

watch(() => store.dialogVisible, (newVal) => {
  if (newVal) {
    selectedStyle.value = 'style_multi_1';
    uploadedFiles.value = [];
    availableResources.value = [];
    currentQuery.value = '';
    page.value = 1;
    hasMore.value = true;

    const resourceType = store.currentLibrary.resource_type;
    const resourceId = store.currentLibrary.resource_id;

    if (resourceType && resourceType !== 'all' && resourceType !== 'rsshub') {
      loadMore();
    }

    setTimeout(() => {
      scrollWrapper = document.querySelector('.resource-select-popper .el-scrollbar__wrap');
      if (scrollWrapper) {
        scrollWrapper.addEventListener('scroll', handleScroll);
      }
    }, 300);
    
    if (store.isEditing && resourceId) {
        if (resourceType === 'person') {
            if(store.personNameCache[resourceId]){
                 availableResources.value = [{id: resourceId, name: store.personNameCache[resourceId]}];
            } else {
                 api.resolveItem(resourceId).then(res => {
                     availableResources.value = [res.data];
                     store.personNameCache[res.data.id] = res.data.name;
                 });
            }
        } else {
             const resourceKeyMap = {
                collection: 'collections', tag: 'tags', genre: 'genres', studio: 'studios',
             };
             const key = resourceKeyMap[resourceType];
             const found = store.classifications[key]?.find(item => item.id === resourceId);
             if(found) availableResources.value = [found];
        }
    } else {
        availableResources.value = [];
    }
  } else {
    if (scrollWrapper) {
      scrollWrapper.removeEventListener('scroll', handleScroll);
      scrollWrapper = null;
    }
  }
});

watch(() => store.currentLibrary.resource_type, (newVal, oldVal) => {
  if (store.dialogVisible && newVal !== oldVal) {
    store.currentLibrary.resource_id = '';
    searchResource('');
  }
});
</script>

<style scoped>
.lib-form {
  max-height: 65vh;
  overflow-y: auto;
  padding-right: 8px;
}

.button-tips {
  margin-left: 10px;
  line-height: 1.4;
  align-self: center;
}
.tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin: 0;
  padding: 0;
}
.tip-warning {
    color: #E6A23C;
}
.cover-preview-wrapper {
  width: 200px;
  height: 112.5px;
  background-color: var(--el-fill-color-darker);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.cover-preview-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.cover-preview-placeholder {
  color: var(--el-text-color-secondary);
  font-size: 14px;
}
.loading-indicator {
  padding: 10px 0;
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 14px;
}

@media (max-width: 768px) {
  .lib-form {
    max-height: 60vh;
  }

  .lib-form :deep(.el-form-item__label) {
    font-size: 13px;
  }

  .button-tips {
    margin-left: 0;
    margin-top: 8px;
  }
}
</style>
