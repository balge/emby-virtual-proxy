<template>
  <div class="login-wrapper">
    <div class="login-card">
      <div class="login-header">
        <h1 class="login-title">Emby Virtual Proxy</h1>
        <p class="login-subtitle">请登录以继续</p>
      </div>
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent="handleLogin"
      >
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="form.username"
            placeholder="请输入用户名"
            :prefix-icon="User"
            size="large"
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            show-password
            :prefix-icon="Lock"
            size="large"
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            size="large"
            class="login-btn"
            :loading="loading"
            @click="handleLogin"
          >
            登 录
          </el-button>
        </el-form-item>
      </el-form>
      <p v-if="errorMsg" class="login-error">{{ errorMsg }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue';
import { useRouter } from 'vue-router';
import { User, Lock } from '@element-plus/icons-vue';
import api from '@/api';

const router = useRouter();

const formRef = ref(null);
const loading = ref(false);
const errorMsg = ref('');

const form = reactive({
  username: '',
  password: '',
});

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
};

const handleLogin = async () => {
  if (!formRef.value) return;
  await formRef.value.validate(async (valid) => {
    if (!valid) return;
    loading.value = true;
    errorMsg.value = '';
    try {
      const res = await api.login(form.username, form.password);
      const token = res.data.token;
      if (token) {
        localStorage.setItem('auth_token', token);
      }
      router.push({ name: 'Home' });
    } catch (err) {
      errorMsg.value = err.response?.data?.detail || '登录失败，请检查网络';
    } finally {
      loading.value = false;
    }
  });
};
</script>

<style scoped>
.login-wrapper {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-bg);
  padding: 20px;
}

.login-card {
  width: 100%;
  max-width: 400px;
  padding: 40px 32px;
  background: var(--el-bg-color);
  border-radius: 16px;
  box-shadow: var(--card-shadow);
}

.login-header {
  text-align: center;
  margin-bottom: 32px;
}

.login-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--el-text-color-primary);
  margin: 0 0 8px 0;
}

.login-subtitle {
  font-size: 14px;
  color: var(--el-text-color-secondary);
  margin: 0;
}

.login-btn {
  width: 100%;
  margin-top: 8px;
}

.login-error {
  text-align: center;
  color: var(--el-color-danger);
  font-size: 13px;
  margin: 12px 0 0 0;
}

@media (max-width: 480px) {
  .login-card {
    padding: 32px 20px;
  }
}
</style>
