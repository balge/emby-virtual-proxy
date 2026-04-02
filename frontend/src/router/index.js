import { createRouter, createWebHistory } from 'vue-router'
import api from '@/api'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/LoginPage.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/',
    name: 'Home',
    component: () => import('@/views/HomePage.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('@/views/SettingsPage.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/virtual',
    name: 'Virtual',
    component: () => import('@/views/VirtualLibrariesPage.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/filters',
    name: 'Filters',
    component: () => import('@/views/FiltersPage.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/libraries',
    name: 'Libraries',
    component: () => import('@/views/LibrariesPage.vue'),
    meta: { requiresAuth: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

let _authEnabled = null

async function isAuthEnabled() {
  if (_authEnabled !== null) return _authEnabled
  try {
    const res = await api.getAuthStatus()
    _authEnabled = res.data.auth_enabled
    return _authEnabled
  } catch {
    return false
  }
}

async function isTokenValid() {
  const token = localStorage.getItem('auth_token')
  if (!token) return false
  try {
    await api.getConfig()
    return true
  } catch {
    localStorage.removeItem('auth_token')
    return false
  }
}

router.beforeEach(async (to, from, next) => {
  const authEnabled = await isAuthEnabled()

  if (!authEnabled) {
    if (to.name === 'Login') return next({ name: 'Home' })
    return next()
  }

  if (to.meta.requiresAuth) {
    const valid = await isTokenValid()
    if (!valid) return next({ name: 'Login' })
    return next()
  }

  if (to.name === 'Login') {
    const valid = await isTokenValid()
    if (valid) return next({ name: 'Home' })
  }

  next()
})

export function resetAuthCache() {
  _authEnabled = null
}

export default router
