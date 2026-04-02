<template>
  <el-card class="box-card" shadow="never">
    <template #header>
      <div class="card-header">
        <span class="card-title">媒体库管理</span>
        <div class="header-actions">
          <el-button @click="store.syncRealLibraries()" :loading="store.dataLoading">从 Emby 同步</el-button>
          <el-button @click="store.refreshAllRealLibraryCovers()">刷新全部封面</el-button>
          <el-button type="primary" @click="store.saveRealLibraries()" :loading="store.saving">保存配置</el-button>
        </div>
      </div>
    </template>

    <div class="cron-section">
      <el-form-item label="封面定时刷新（Cron 表达式）">
        <el-input
          v-model="store.config.real_library_cover_cron"
          placeholder="例如: 0 3 * * *（每天凌晨3点）"
          style="max-width: 300px;"
        />
        <div class="form-item-description">
          留空则不自动刷新。保存系统设置后生效。格式：分 时 日 月 周。
        </div>
      </el-form-item>
    </div>

    <!-- Desktop table -->
    <div class="table-view" v-if="store.config.real_libraries?.length">
      <el-table :data="store.config.real_libraries" stripe style="width: 100%;">
        <el-table-column label="启用" width="70" align="center">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" size="small" />
          </template>
        </el-table-column>
        <el-table-column label="封面" width="200" align="center" class-name="cover-col">
          <template #default="{ row }">
            <img v-if="row.image_tag" class="cover-thumb" :src="`/covers/${row.id}.jpg?t=${row.image_tag}`" alt="封面" />
            <span v-else class="cover-empty">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="库名称" min-width="120" />
        <el-table-column label="生成封面" width="90" align="center">
          <template #default="{ row }">
            <el-switch v-model="row.cover_enabled" size="small" />
          </template>
        </el-table-column>
        <el-table-column label="封面中文标题" min-width="140">
          <template #default="{ row }">
            <el-input v-model="row.cover_title_zh" size="small" :placeholder="row.name" :disabled="!row.cover_enabled" />
          </template>
        </el-table-column>
        <el-table-column label="封面英文标题" min-width="140">
          <template #default="{ row }">
            <el-input v-model="row.cover_title_en" size="small" placeholder="可选" :disabled="!row.cover_enabled" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="center">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click="store.refreshRealLibraryCover(row.id)" :disabled="!row.cover_enabled">刷新封面</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Mobile card view -->
    <div class="card-view" v-if="store.config.real_libraries?.length">
      <div v-for="row in store.config.real_libraries" :key="row.id" class="lib-card">
        <div class="lib-card-top">
          <img v-if="row.image_tag" class="cover-thumb-card" :src="`/covers/${row.id}.jpg?t=${row.image_tag}`" alt="封面" />
          <div class="lib-card-info">
            <span class="lib-card-name">{{ row.name }}</span>
            <div class="lib-card-switch-row">
              <div class="lib-card-switch-item">
                <span class="switch-label-sm">启用</span>
                <el-switch v-model="row.enabled" size="small" />
              </div>
              <div class="lib-card-switch-item">
                <span class="switch-label-sm">生成封面</span>
                <el-switch v-model="row.cover_enabled" size="small" />
              </div>
            </div>
          </div>
        </div>
        <div class="lib-card-inputs" v-if="row.cover_enabled">
          <el-input v-model="row.cover_title_zh" size="small" :placeholder="row.name" />
          <el-input v-model="row.cover_title_en" size="small" placeholder="英文标题（可选）" />
        </div>
        <div class="lib-card-actions">
          <el-button size="small" type="primary" plain @click="store.refreshRealLibraryCover(row.id)" :disabled="!row.cover_enabled">刷新封面</el-button>
        </div>
      </div>
    </div>

    <el-empty v-if="!store.config.real_libraries?.length" description="暂无真实库数据，请点击「从 Emby 同步」" />
  </el-card>
</template>

<script setup>
import { useMainStore } from '@/stores/main';
const store = useMainStore();

const getCoverUrl = (row) => {
  if (row.image_tag) return `/covers/${row.id}.jpg?t=${row.image_tag}`;
  const embyUrl = (store.config.emby_url || '').replace(/\/+$/, '');
  if (embyUrl) return `${embyUrl}/emby/Items/${row.id}/Images/Primary`;
  return '';
};
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.card-title { font-size: 16px; font-weight: 600; }
.header-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.cron-section { margin-bottom: 16px; }
.form-item-description {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
  margin-top: 4px;
}

/* Cover thumbnail */
.cover-thumb {
  max-width: 100%;
  height: auto;
  border-radius: 6px;
  border: 1px solid var(--el-border-color-lighter);
}
.cover-empty {
  color: var(--el-text-color-placeholder);
  font-size: 18px;
}

/* Mobile card view */
.card-view { display: none; }
.lib-card {
  padding: 14px;
  border-radius: 10px;
  background: var(--el-fill-color-lighter);
  margin-bottom: 12px;
}
.lib-card-top { display: flex; gap: 12px; align-items: flex-start; }
.cover-thumb-card {
  width: 120px;
  height: auto;
  border-radius: 6px;
  border: 1px solid var(--el-border-color-lighter);
  flex-shrink: 0;
}
.lib-card-info { flex: 1; min-width: 0; }
.lib-card-name { font-weight: 600; font-size: 15px; display: block; margin-bottom: 8px; }
.lib-card-switch-row {
  display: flex;
  gap: 16px;
  align-items: center;
}
.lib-card-switch-item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.switch-label-sm { font-size: 12px; color: var(--el-text-color-secondary); }
.lib-card-inputs { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; }
.lib-card-actions { margin-top: 10px; }

@media (max-width: 900px) {
  .table-view { display: none; }
  .card-view { display: block; }
  .card-header { flex-direction: column; align-items: flex-start; }
  .header-actions { width: 100%; }
}
</style>
