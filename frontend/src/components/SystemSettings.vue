<template>
  <el-card class="box-card" shadow="never">
    <template #header>
      <div class="card-header">
        <span class="card-title">系统设置</span>
        <el-button type="primary" @click="store.saveConfig()" :loading="store.saving">
          保存所有设置
        </el-button>
      </div>
    </template>
    <el-form label-position="top" label-width="100px" v-if="store.config" class="settings-form">

      <div class="form-section">
        <div class="form-row">
          <el-form-item label="Emby 服务器地址（如果使用了302，请填写302地址）" class="form-item-flex">
            <el-input
              v-model="store.config.emby_url"
              placeholder="例如: http://192.168.1.10:8096"
            />
            <div class="form-item-description">
              您的 Emby 或 Jellyfin 服务器的完整访问地址。
            </div>
          </el-form-item>
        </div>

        <div class="form-row two-col">
          <el-form-item label="Emby API 密钥" class="form-item-flex">
            <el-input
              v-model="store.config.emby_api_key"
              type="password"
              show-password
              placeholder="请输入您的 API Key"
            />
            <div class="form-item-description">
              请在 Emby 后台 -> API 密钥 中生成一个新的 API Key。
            </div>
          </el-form-item>

          <el-form-item label="TMDB API 密钥" class="form-item-flex">
            <el-input
              v-model="store.config.tmdb_api_key"
              type="password"
              show-password
              placeholder="请输入您的 TMDB API Key"
            />
            <div class="form-item-description">
              用于从 TMDB 获取缺失的剧集信息。请从 The Movie Database (TMDB) 官网申请。
            </div>
          </el-form-item>
        </div>

        <div class="form-row two-col">
          <el-form-item label="TMDB HTTP 代理" class="form-item-flex">
            <el-input
              v-model="store.config.tmdb_proxy"
              placeholder="例如: http://127.0.0.1:7890"
            />
            <div class="form-item-description">
              如果您的服务器无法直接访问 TMDB API，请在此处填写 HTTP 代理地址。
            </div>
          </el-form-item>

          <el-form-item label="RSS 虚拟库定时刷新间隔（小时）" class="form-item-flex">
            <el-input-number v-model="store.config.rss_refresh_interval" :min="0" />
            <div class="form-item-description">
              设置 RSS 虚拟库自动刷新的时间间隔，单位为小时。设置为 0 表示禁用定时刷新。
            </div>
          </el-form-item>
        </div>
      </div>

      <el-divider />

      <div class="form-section">
        <div class="switch-grid">
          <div class="switch-item">
            <div class="switch-row">
              <span class="switch-label">启用内存缓存</span>
              <el-switch v-model="store.config.enable_cache" />
            </div>
            <div class="form-item-description">
              开启后，代理服务器会缓存 Emby API 的响应以提高性能。
            </div>
          </div>

          <div class="switch-item">
            <div class="switch-row">
              <span class="switch-label">显示缺失的剧集</span>
              <el-switch v-model="store.config.show_missing_episodes" />
            </div>
            <div class="form-item-description">
              开启后，进入剧集列表时，会自动从 TMDB 查询并显示本地缺失的剧集。
            </div>
          </div>

          <div class="switch-item">
            <div class="switch-row">
              <span class="switch-label">全局强制按 TMDB ID 合并</span>
              <el-switch v-model="store.config.force_merge_by_tmdb_id" />
            </div>
            <div class="form-item-description">
              开启后，将无视虚拟库的独立设置，强制对所有内容进行 TMDB ID 合并。
            </div>
          </div>
        </div>
      </div>

      <el-divider />

      <div class="form-section">
        <div class="form-row two-col">
          <el-form-item label="自动生成封面默认样式" class="form-item-flex">
            <el-select v-model="store.config.default_cover_style" placeholder="请选择默认样式" style="width: 100%;">
              <el-option label="样式一 (多图)" value="style_multi_1"></el-option>
              <el-option label="样式二 (单图)" value="style_single_1"></el-option>
              <el-option label="样式三 (单图)" value="style_single_2"></el-option>
            </el-select>
            <div class="form-item-description">
              此处选择的样式，将作为触发封面"自动生成"时的默认样式。
            </div>
          </el-form-item>

          <el-form-item label="自定义中文字体路径 (可选)" class="form-item-flex">
            <el-input
              v-model="store.config.custom_zh_font_path"
              placeholder="e.g., /config/fonts/myfont.ttf"
            />
            <div class="form-item-description">
              留空则使用默认字体。请确保路径在 Docker 容器中可访问。
            </div>
          </el-form-item>
        </div>

        <div class="form-row two-col">
          <el-form-item label="全局自定义图片目录 (可选)" class="form-item-flex">
            <el-input
              v-model="store.config.custom_image_path"
              placeholder="e.g., /config/images/custom"
            />
            <div class="form-item-description">
              留空则默认从虚拟库内容中下载封面。
            </div>
          </el-form-item>

          <el-form-item label="自定义英文字体路径 (可选)" class="form-item-flex">
            <el-input
              v-model="store.config.custom_en_font_path"
              placeholder="e.g., /config/fonts/myfont.otf"
            />
            <div class="form-item-description">
              留空则使用默认字体。请确保路径在 Docker 容器中可访问。
            </div>
          </el-form-item>
        </div>
      </div>

      <el-divider />

      <div class="form-section">
        <el-form-item label="全局隐藏类型">
          <el-select
            v-model="store.config.hide"
            multiple
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入类型 (如 'music') 将被全局隐藏"
            style="width: 100%;"
          >
            <el-option
              v-for="item in collectionTypes"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <div class="form-item-description">
            在这里选择或输入的类型将被默认隐藏。您可以在"调整主页布局"中覆盖此设置。
          </div>
        </el-form-item>

        <el-form-item label="忽略媒体库">
          <el-select
            v-model="store.config.ignore_libraries"
            multiple
            filterable
            collapse-tags
            collapse-tags-tooltip
            placeholder="选择要忽略的真实媒体库"
            style="width: 100%;"
          >
            <el-option
              v-for="lib in realLibraries"
              :key="lib.id"
              :label="lib.name"
              :value="lib.id"
            />
          </el-select>
          <div class="form-item-description">
            被忽略的媒体库不会出现在虚拟库的「全库」查询范围和「源库范围」选择列表中。
          </div>
        </el-form-item>
      </div>

      <el-divider />

      <div class="form-section danger-zone">
        <el-form-item label="危险区域">
          <el-popconfirm
              title="确定要清空所有本地生成的封面吗？"
              width="280"
              confirm-button-text="确定清空"
              cancel-button-text="取消"
              @confirm="store.clearAllCovers()"
          >
              <template #reference>
                  <el-button type="danger" :loading="store.saving">清空所有本地封面</el-button>
              </template>
          </el-popconfirm>
          <div class="form-item-description">
            此操作将删除 config/images 目录下的所有图片和临时文件，并重置所有虚拟库的封面状态。此操作不可逆。
          </div>
        </el-form-item>
      </div>

    </el-form>
  </el-card>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useMainStore } from '@/stores/main';

const store = useMainStore();

const realLibraries = computed(() => {
  return (store.allLibrariesForSorting || []).filter(lib => lib.type === 'real');
});

const collectionTypes = ref([
  { value: 'movies', label: '电影 (movies)' },
  { value: 'tvshows', label: '电视剧 (tvshows)' },
  { value: 'music', label: '音乐 (music)' },
  { value: 'playlists', label: '播放列表 (playlists)' },
  { value: 'musicvideos', label: '音乐视频 (musicvideos)' },
  { value: 'livetv', label: '电视直播 (livetv)' },
  { value: 'boxsets', label: '合集 (boxsets)' },
  { value: 'photos', label: '照片 (photos)' },
  { value: 'homevideos', label: '家庭视频 (homevideos)' },
  { value: 'books', label: '书籍 (books)' },
]);
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
}

.settings-form {
  max-width: 100%;
}

.form-section {
  margin-bottom: 4px;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0 24px;
}

.form-row.two-col {
  grid-template-columns: 1fr 1fr;
}

.form-item-flex {
  min-width: 0;
}

.form-item-description {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
  margin-top: 4px;
}

/* Switch grid layout */
.switch-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.switch-item {
  padding: 16px;
  border-radius: 10px;
  background: var(--el-fill-color-lighter);
  transition: background var(--transition-fast);
}

.switch-item:hover {
  background: var(--el-fill-color-light);
}

.switch-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.switch-label {
  font-size: 14px;
  font-weight: 500;
  color: var(--el-text-color-primary);
}

/* Danger zone */
.danger-zone {
  padding: 16px;
  border-radius: 10px;
  border: 1px dashed var(--el-color-danger-light-5);
  background: var(--el-color-danger-light-9);
}

/* Responsive */
@media (max-width: 768px) {
  .form-row.two-col {
    grid-template-columns: 1fr;
  }

  .switch-grid {
    grid-template-columns: 1fr;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .card-header .el-button {
    width: 100%;
  }
}
</style>
