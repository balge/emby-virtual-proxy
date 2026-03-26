<template>
  <router-view />
</template>

<script setup>
import { onMounted, onBeforeUnmount } from 'vue';
import { useRouter } from 'vue-router';
import { resetAuthCache } from '@/router';

const router = useRouter();

const initTheme = () => {
  const savedTheme = localStorage.getItem('theme');
  const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const html = document.documentElement;
  if (savedTheme === 'dark' || (!savedTheme && prefersDark)) {
    html.classList.add('dark');
  } else {
    html.classList.remove('dark');
  }
};

const onAuthExpired = () => {
  localStorage.removeItem('auth_token');
  resetAuthCache();
  router.push({ name: 'Login' });
};

onMounted(() => {
  initTheme();
  window.addEventListener('auth-expired', onAuthExpired);
});

onBeforeUnmount(() => {
  window.removeEventListener('auth-expired', onAuthExpired);
});
</script>
