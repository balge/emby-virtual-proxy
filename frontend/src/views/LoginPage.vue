<template>
  <div class="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950 px-4">
    <div class="w-full max-w-sm">
      <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-lg p-8">
        <div class="text-center mb-8">
          <img src="/logo.png" alt="Logo" class="w-16 h-16 mx-auto mb-3 rounded-xl" />
          <h1 class="text-xl font-bold text-gray-900 dark:text-gray-100">Emby Virtual Proxy</h1>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">请登录以继续</p>
        </div>

        <form @submit.prevent="handleLogin" class="space-y-4">
          <BaseInput
            v-model="form.username"
            label="用户名"
            placeholder="请输入用户名"
            required
            @enter="handleLogin"
          />
          <BaseInput
            v-model="form.password"
            label="密码"
            type="password"
            placeholder="请输入密码"
            required
            @enter="handleLogin"
          />
          <BaseButton
            variant="primary"
            size="lg"
            html-type="submit"
            :loading="loading"
            class="w-full"
          >
            登 录
          </BaseButton>
        </form>

        <p v-if="errorMsg" class="mt-4 text-center text-sm text-red-500">{{ errorMsg }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import api from '@/api'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'

const router = useRouter()
const loading = ref(false)
const errorMsg = ref('')
const form = reactive({ username: '', password: '' })

const handleLogin = async () => {
  if (!form.username || !form.password) { errorMsg.value = '请填写用户名和密码'; return }
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await api.login(form.username, form.password)
    if (res.data.token) localStorage.setItem('auth_token', res.data.token)
    router.push({ name: 'Home' })
  } catch (err) {
    errorMsg.value = err.response?.data?.detail || '登录失败，请检查网络'
  } finally {
    loading.value = false
  }
}
</script>
