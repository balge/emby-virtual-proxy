<template>
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
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { useRouter, useRoute } from "vue-router";
import { resetAuthCache } from "@/router";
import api from "@/api";
import AppSidebar from "@/components/AppSidebar.vue";
import AppMobileNav from "@/components/AppMobileNav.vue";
import Toast from "@/components/ui/Toast.vue";

const router = useRouter();
const route = useRoute();
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
