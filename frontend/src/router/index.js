import { createRouter, createWebHistory } from 'vue-router';
import api from '@/api';

const LoginPage = () => import('@/views/LoginPage.vue');
const HomePage = () => import('@/views/HomePage.vue');

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: LoginPage,
    meta: { requiresAuth: false },
  },
  {
    path: '/',
    name: 'Home',
    component: HomePage,
    meta: { requiresAuth: true },
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// 缓存认证状态，避免每次路由跳转都请求后端
let _authEnabled = null;

async function isAuthEnabled() {
  if (_authEnabled !== null) return _authEnabled;
  try {
    const res = await api.getAuthStatus();
    _authEnabled = res.data.auth_enabled;
    return _authEnabled;
  } catch {
    return false;
  }
}

async function isTokenValid() {
  const token = localStorage.getItem('auth_token');
  if (!token) return false;
  try {
    await api.getConfig();
    return true;
  } catch {
    localStorage.removeItem('auth_token');
    return false;
  }
}

router.beforeEach(async (to, from, next) => {
  const authEnabled = await isAuthEnabled();

  if (!authEnabled) {
    // 认证未启用，登录页直接跳首页
    if (to.name === 'Login') return next({ name: 'Home' });
    return next();
  }

  // 认证已启用
  if (to.meta.requiresAuth) {
    const valid = await isTokenValid();
    if (!valid) return next({ name: 'Login' });
    return next();
  }

  // 已登录用户访问 /login，跳首页
  if (to.name === 'Login') {
    const valid = await isTokenValid();
    if (valid) return next({ name: 'Home' });
  }

  next();
});

// 暴露重置方法，登出时清除缓存
export function resetAuthCache() {
  _authEnabled = null;
}

export default router;
