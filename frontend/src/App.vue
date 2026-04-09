<template>
  <div>
    <div v-if="isLoginRoute" class="min-h-screen">
      <router-view />
    </div>
    <div v-else class="min-h-screen">
      <AppSidebar :auth-enabled="authEnabled" @logout="handleLogout" />
      <AppMobileNav :auth-enabled="authEnabled" @logout="handleLogout" />

      <main
        class="lg:pl-60 pt-[calc(3.5rem+env(safe-area-inset-top,0px))] lg:pt-0 pb-[calc(5rem+env(safe-area-inset-bottom,0px))] lg:pb-0"
      >
        <div class="mx-auto px-4 sm:px-6 lg:px-8 py-6">
          <router-view />
        </div>
      </main>
    </div>

    <Toast />

    <div
      v-if="mainStore.switchingServer"
      class="fixed inset-0 z-[100] bg-black/40 backdrop-blur-[1px] flex items-center justify-center"
    >
      <div
        class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-5 py-4 shadow-lg min-w-[220px]"
      >
        <div class="flex items-center gap-3">
          <svg
            class="animate-spin h-5 w-5 text-primary-500"
            viewBox="0 0 24 24"
            fill="none"
          >
            <circle
              class="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              stroke-width="4"
            />
            <path
              class="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          <div>
            <p class="text-sm font-medium text-gray-900 dark:text-gray-100">
              正在切换服务器
            </p>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              请稍候，正在刷新配置与数据...
            </p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { useRouter, useRoute } from "vue-router";
import { useMainStore } from "@/stores/main";
import { resetAuthCache } from "@/router";
import api from "@/api";
import AppSidebar from "@/components/AppSidebar.vue";
import AppMobileNav from "@/components/AppMobileNav.vue";
import Toast from "@/components/ui/Toast.vue";

const router = useRouter();
const route = useRoute();
const mainStore = useMainStore();
const authEnabled = ref(false);

const isLoginRoute = computed(() => route.name === "Login");

const initTheme = () => {
  const saved = localStorage.getItem("theme");
  const prefersDark = window.matchMedia?.(
    "(prefers-color-scheme: dark)",
  ).matches;
  if (saved === "dark" || (!saved && prefersDark)) {
    document.documentElement.classList.add("dark");
  }
};

const handleLogout = async () => {
  try {
    await api.logout();
  } catch {}
  localStorage.removeItem("auth_token");
  resetAuthCache();
  router.push({ name: "Login" });
};

const onAuthExpired = () => {
  localStorage.removeItem("auth_token");
  resetAuthCache();
  router.push({ name: "Login" });
};

onMounted(async () => {
  initTheme();
  window.addEventListener("auth-expired", onAuthExpired);
  try {
    const res = await api.getAuthStatus();
    authEnabled.value = res.data.auth_enabled;
  } catch {}
});

onBeforeUnmount(() => {
  window.removeEventListener("auth-expired", onAuthExpired);
});
</script>
