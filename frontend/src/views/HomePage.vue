<template>
  <el-container class="main-container">
    <el-header class="main-header">
      <div class="title-area">
        <h1 class="title">Emby Virtual Proxy 配置面板</h1>
      </div>

      <div class="controls-area">
        <el-switch
          v-model="isDarkMode"
          class="theme-switch"
          inline-prompt
          :active-icon="Moon"
          :inactive-icon="Sunny"
          @change="toggleDark"
        />
        
        <div class="status-area" v-if="store.dataStatus">
          <el-tag :type="store.dataStatus.type === 'error' ? 'danger' : 'success'" effect="plain" round>
            {{ store.dataStatus.text }}
          </el-tag>
        </div>

        <el-button
          v-if="authEnabled"
          text
          @click="handleLogout"
          class="logout-btn"
        >
          退出登录
        </el-button>
      </div>
    </el-header>

    <el-main class="main-content">
      <el-tabs v-model="activeTab" class="main-tabs">
        <el-tab-pane label="核心设置" name="core">
          <div class="settings-grid">
            <SystemSettings />
            <VirtualLibraries />
          </div>
        </el-tab-pane>
        <el-tab-pane label="高级筛选器" name="filters">
          <AdvancedFilterManager />
        </el-tab-pane>
      </el-tabs>
    </el-main>
  </el-container>

  <LibraryEditDialog />
  <DisplayOrderManager />
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useMainStore } from '@/stores/main';
import { Sunny, Moon } from '@element-plus/icons-vue';
import { resetAuthCache } from '@/router';
import api from '@/api';
import SystemSettings from '@/components/SystemSettings.vue';
import VirtualLibraries from '@/components/VirtualLibraries.vue';
import AdvancedFilterManager from '@/components/AdvancedFilterManager.vue';
import LibraryEditDialog from '@/components/LibraryEditDialog.vue';
import DisplayOrderManager from '@/components/DisplayOrderManager.vue';

const store = useMainStore();
const router = useRouter();
const isDarkMode = ref(false);
const activeTab = ref('core');
const authEnabled = ref(false);

const toggleDark = (value) => {
  const html = document.documentElement;
  if (value) {
    html.classList.add('dark');
    localStorage.setItem('theme', 'dark');
  } else {
    html.classList.remove('dark');
    localStorage.setItem('theme', 'light');
  }
  isDarkMode.value = value;
};

const handleLogout = async () => {
  try { await api.logout(); } catch { /* ignore */ }
  localStorage.removeItem('auth_token');
  resetAuthCache();
  router.push({ name: 'Login' });
};

onMounted(async () => {
  // 初始化主题
  const savedTheme = localStorage.getItem('theme');
  const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  if (savedTheme === 'dark' || (!savedTheme && prefersDark)) {
    toggleDark(true);
  }

  // 检查认证状态
  try {
    const res = await api.getAuthStatus();
    authEnabled.value = res.data.auth_enabled;
  } catch { /* ignore */ }

  // 加载数据
  store.fetchAllInitialData();
});
</script>

<style scoped>
.main-container {
  max-width: 1400px;
  margin: 0 auto;
  padding: 24px 32px;
  min-height: 100vh;
}

.main-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding: 16px 24px;
  background: var(--app-header-bg);
  backdrop-filter: blur(12px);
  border-radius: var(--card-border-radius);
  box-shadow: var(--app-header-shadow);
  height: auto;
  position: sticky;
  top: 12px;
  z-index: 100;
}

.title-area {
  display: flex;
  align-items: center;
  min-width: 0;
}

.title {
  font-size: 1.35rem;
  font-weight: 700;
  margin: 0;
  color: var(--el-text-color-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  letter-spacing: -0.01em;
}

.controls-area {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-shrink: 0;
}

.status-area .el-tag {
  font-size: 13px;
}

.logout-btn {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.settings-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 24px;
}

.main-content {
  padding: 0;
}

/* Theme switch */
.theme-switch {
  --el-switch-on-color: #2c2c2c;
  --el-switch-off-color: #dcdfe6;
  --el-switch-border-color: var(--el-border-color);
}
.theme-switch .el-switch__core .el-icon {
  color: #303133;
}
.dark .theme-switch .el-switch__core .el-icon {
  color: #999;
}
.theme-switch .is-active .el-icon {
  color: #fff;
}

/* Responsive */
@media (max-width: 768px) {
  .main-container {
    padding: 12px;
  }
  .main-header {
    flex-direction: column;
    gap: 12px;
    align-items: flex-start;
    padding: 14px 16px;
    position: static;
  }
  .title {
    font-size: 1.1rem;
  }
  .controls-area {
    width: 100%;
    justify-content: space-between;
  }
  .settings-grid {
    gap: 16px;
  }
}

@media (min-width: 769px) and (max-width: 1024px) {
  .main-container {
    padding: 20px 24px;
  }
}
</style>
