<template>
  <div>
    <h1 class="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-1">
      欢迎回来
    </h1>
    <p class="text-sm text-gray-500 dark:text-gray-400 mb-8">
      Emby Virtual Proxy 配置面板
    </p>

    <!-- Server management -->
    <div
      v-if="store.config?.servers?.length"
      class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-4 mb-8"
    >
      <div class="flex items-center justify-between gap-3 mb-3">
        <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100">
          当前管理服务器
        </h2>
        <BaseButton variant="secondary" @click="openCreateServerDialog"
          >添加服务器</BaseButton
        >
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div
          v-for="srv in store.config.servers"
          :key="srv.id"
          class="rounded-xl border p-3 transition-colors shadow-sm"
          :class="
            String(srv.id) === String(store.config.admin_active_server_id)
              ? 'border-primary-400 bg-primary-50/60 dark:border-primary-500 dark:bg-slate-900/80'
              : 'border-gray-200 bg-white dark:border-slate-700 dark:bg-slate-800/70'
          "
        >
          <div class="flex items-center justify-between gap-2">
            <p
              class="text-sm font-medium text-gray-900 dark:text-gray-100 truncate"
            >
              {{ srv.name || "Emby" }}
            </p>
            <span
              class="text-[10px] px-2 py-0.5 rounded-full"
              :class="
                String(srv.id) === String(store.config.admin_active_server_id)
                  ? 'bg-primary-100 text-primary-700 dark:bg-primary-700/40 dark:text-primary-100'
                  : 'bg-gray-100 text-gray-600 dark:bg-slate-700 dark:text-slate-200'
              "
            >
              {{
                String(srv.id) === String(store.config.admin_active_server_id)
                  ? "当前"
                  : "可切换"
              }}
            </span>
          </div>
          <div class="mt-2 space-y-1 text-xs text-gray-500 dark:text-slate-300">
            <p class="truncate">地址：{{ srv.emby_url || "-" }}</p>
            <p>代理端口：{{ srv.proxy_port ?? "-" }}</p>
          </div>
          <div
            class="mt-3 flex items-center gap-2 rounded-lg px-2 py-2"
            :class="
              String(srv.id) === String(store.config.admin_active_server_id)
                ? 'bg-white/70 dark:bg-slate-800/70'
                : 'bg-gray-50 dark:bg-slate-800/40'
            "
          >
            <BaseButton
              size="sm"
              class="!font-medium dark:!border-slate-500 dark:!text-slate-100 dark:hover:!border-slate-400 dark:hover:!bg-slate-700/70"
              :disabled="
                String(srv.id) === String(store.config.admin_active_server_id)
              "
              @click="store.setActiveServer(srv.id)"
            >
              设为当前
            </BaseButton>
            <BaseButton
              size="sm"
              variant="secondary"
              class="!font-medium dark:!border-slate-500 dark:!text-slate-100 dark:hover:!border-slate-400 dark:hover:!bg-slate-700/70"
              @click="openEditServerDialog(srv)"
            >
              编辑
            </BaseButton>
            <BaseButton
              size="sm"
              variant="danger-outline"
              class="dark:!border-red-500/60 dark:!text-red-300 dark:hover:!bg-red-900/30 dark:hover:!border-red-400"
              :disabled="
                (store.config.servers || []).length <= 1 ||
                String(srv.id) === String(store.config.admin_active_server_id)
              "
              @click="openDeleteServerConfirm(srv)"
            >
              删除
            </BaseButton>
          </div>
        </div>
      </div>
      <p class="mt-2 text-xs text-gray-500 dark:text-gray-400">
        提示：代理访问入口为
        <span class="font-mono">http://&lt;代理IP&gt;:&lt;代理端口&gt;</span
        >，端口需在 compose 中手动映射后才可从局域网访问。
      </p>
    </div>

    <!-- Status -->
    <div
      v-if="store.dataLoading"
      class="flex items-center gap-2 text-sm text-gray-500 mb-6"
    >
      <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
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
      正在加载数据...
    </div>

    <!-- Quick stats -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
      <div
        class="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm border border-gray-200 dark:border-gray-700"
      >
        <p class="text-2xl font-bold text-primary-600">
          {{ store.virtualLibraries.length }}
        </p>
        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">虚拟媒体库</p>
      </div>
      <div
        class="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm border border-gray-200 dark:border-gray-700"
      >
        <p class="text-2xl font-bold text-emerald-600">
          {{ (store.config.real_libraries || []).length }}
        </p>
        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">真实媒体库</p>
      </div>
      <div
        class="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm border border-gray-200 dark:border-gray-700"
      >
        <p class="text-2xl font-bold text-amber-600">
          {{ (store.config.advanced_filters || []).length }}
        </p>
        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">高级筛选器</p>
      </div>
    </div>

    <!-- Quick links -->
    <h2
      class="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3"
    >
      快捷操作
    </h2>
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <router-link
        v-for="link in quickLinks"
        :key="link.to"
        :to="link.to"
        class="flex items-center gap-3 p-4 bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 hover:border-primary-300 dark:hover:border-primary-700 transition-colors group"
      >
        <div
          class="w-10 h-10 rounded-lg flex items-center justify-center shrink-0"
          :class="link.iconBg"
        >
          <component :is="link.icon" class="w-5 h-5" :class="link.iconColor" />
        </div>
        <div>
          <p
            class="text-sm font-medium text-gray-900 dark:text-gray-100 group-hover:text-primary-600 dark:group-hover:text-primary-400 transition-colors"
          >
            {{ link.label }}
          </p>
          <p class="text-xs text-gray-500 dark:text-gray-400">
            {{ link.desc }}
          </p>
        </div>
      </router-link>
    </div>

    <BaseDialog
      :open="createServerDialogOpen"
      title="添加服务器"
      @close="createServerDialogOpen = false"
    >
      <div class="space-y-3">
        <BaseInput
          v-model="newServer.name"
          label="名称"
          placeholder="例如：客厅 Emby"
        />
        <BaseInput
          v-model="newServer.emby_url"
          label="Emby 服务器地址"
          placeholder="http://192.168.1.10:8096"
        />
        <BaseInput
          v-model="newServer.emby_api_key"
          label="Emby API 密钥"
          type="password"
          placeholder="API Key"
        />
        <BaseInput
          v-model.number="newServer.proxy_port"
          label="代理端口"
          type="number"
          placeholder="9000"
        />
      </div>
      <template #footer>
        <BaseButton variant="secondary" @click="createServerDialogOpen = false"
          >取消</BaseButton
        >
        <BaseButton :loading="creatingServer" @click="onCreateServer"
          >保存</BaseButton
        >
      </template>
    </BaseDialog>

    <BaseDialog
      :open="editServerDialogOpen"
      title="编辑服务器"
      @close="editServerDialogOpen = false"
    >
      <div class="space-y-3">
        <BaseInput v-model="editServer.name" label="名称" />
        <BaseInput
          v-model="editServer.emby_url"
          label="Emby 服务器地址"
          placeholder="http://192.168.1.10:8096"
        />
        <BaseInput
          v-model="editServer.emby_api_key"
          label="Emby API 密钥"
          type="password"
          placeholder="API Key"
        />
        <BaseInput
          v-model.number="editServer.proxy_port"
          label="代理端口"
          type="number"
          placeholder="9000"
        />
      </div>
      <template #footer>
        <BaseButton variant="secondary" @click="editServerDialogOpen = false"
          >取消</BaseButton
        >
        <BaseButton :loading="editingServer" @click="onEditServer"
          >保存</BaseButton
        >
      </template>
    </BaseDialog>

    <BaseDialog
      :open="deleteServerDialogOpen"
      title="确认删除服务器"
      @close="deleteServerDialogOpen = false"
    >
      <div class="space-y-2">
        <p class="text-sm text-gray-800 dark:text-gray-200">
          确定要删除服务器
          <span class="font-semibold">{{ deletingServer.name || "Emby" }}</span>
          吗？
        </p>
        <p class="text-xs text-gray-500 dark:text-gray-400">
          删除后将移除该服务器配置，且不可恢复。
        </p>
      </div>
      <template #footer>
        <BaseButton variant="secondary" @click="deleteServerDialogOpen = false"
          >取消</BaseButton
        >
        <BaseButton
          variant="danger"
          :loading="deletingServerLoading"
          @click="confirmDeleteServer"
          >确认删除</BaseButton
        >
      </template>
    </BaseDialog>
  </div>
</template>

<script setup>
import { reactive, ref, onMounted } from "vue";
import { useMainStore } from "@/stores/main";
import BaseButton from "@/components/ui/BaseButton.vue";
import BaseDialog from "@/components/ui/BaseDialog.vue";
import BaseInput from "@/components/ui/BaseInput.vue";
import {
  Cog6ToothIcon,
  RectangleStackIcon,
  FunnelIcon,
  BuildingLibraryIcon,
} from "@heroicons/vue/24/outline";
import { useToast } from "@/composables/useToast";

const store = useMainStore();
const toast = useToast();
const createServerDialogOpen = ref(false);
const creatingServer = ref(false);
const editServerDialogOpen = ref(false);
const editingServer = ref(false);
const editingServerId = ref("");
const deleteServerDialogOpen = ref(false);
const deletingServerLoading = ref(false);
const deletingServer = reactive({ id: "", name: "" });
const newServer = reactive({
  name: "",
  emby_url: "",
  emby_api_key: "",
  proxy_port: 9000,
});
const editServer = reactive({
  name: "",
  emby_url: "",
  emby_api_key: "",
  proxy_port: 9000,
});

function openCreateServerDialog() {
  const used = new Set(
    (store.config.servers || []).map((s) => Number(s.proxy_port)),
  );
  let p = 8999;
  while (used.has(p)) p += 1;
  newServer.name = "";
  newServer.emby_url = "";
  newServer.emby_api_key = "";
  newServer.proxy_port = p;
  createServerDialogOpen.value = true;
}

async function onCreateServer() {
  creatingServer.value = true;
  try {
    await store.createServer(
      {
        name: newServer.name,
        emby_url: newServer.emby_url,
        emby_api_key: newServer.emby_api_key,
        proxy_port: newServer.proxy_port,
      },
      { switchToNew: false, persist: true },
    );
    toast.success("服务器已添加（未切换当前服务器）");
    createServerDialogOpen.value = false;
  } catch (e) {
    toast.error(e?.message || "添加服务器失败");
  } finally {
    creatingServer.value = false;
  }
}

async function onDeleteServer(serverId) {
  try {
    const ok = await store.deleteServer(serverId);
    if (!ok) {
      if (String(serverId) === String(store.config.admin_active_server_id)) {
        toast.warning("当前选中服务器不能删除");
      } else {
        toast.warning("至少保留一个服务器");
      }
      return;
    }
    toast.success("服务器已删除");
  } catch (e) {
    toast.error("删除服务器失败");
  }
}

function openDeleteServerConfirm(srv) {
  deletingServer.id = String(srv.id || "");
  deletingServer.name = srv.name || "Emby";
  deleteServerDialogOpen.value = true;
}

async function confirmDeleteServer() {
  if (!deletingServer.id) return;
  deletingServerLoading.value = true;
  try {
    await onDeleteServer(deletingServer.id);
    deleteServerDialogOpen.value = false;
  } finally {
    deletingServerLoading.value = false;
  }
}

function openEditServerDialog(srv) {
  editingServerId.value = String(srv.id);
  editServer.name = srv.name || "";
  editServer.emby_url = srv.emby_url || "";
  editServer.emby_api_key = srv.emby_api_key || "";
  editServer.proxy_port = Number(srv.proxy_port || 8999);
  editServerDialogOpen.value = true;
}

async function onEditServer() {
  if (!editingServerId.value) return;
  editingServer.value = true;
  try {
    await store.updateServer(editingServerId.value, {
      name: editServer.name,
      emby_url: editServer.emby_url,
      emby_api_key: editServer.emby_api_key,
      proxy_port: editServer.proxy_port,
    });
    toast.success("服务器已更新");
    editServerDialogOpen.value = false;
  } catch (e) {
    toast.error(e?.message || "更新服务器失败");
  } finally {
    editingServer.value = false;
  }
}

const quickLinks = [
  {
    to: "/settings",
    label: "核心设置",
    desc: "Emby连接、缓存、Webhook",
    icon: Cog6ToothIcon,
    iconBg: "bg-blue-100 dark:bg-blue-900/30",
    iconColor: "text-blue-600 dark:text-blue-400",
  },
  {
    to: "/virtual",
    label: "虚拟媒体库",
    desc: "创建和管理虚拟库",
    icon: RectangleStackIcon,
    iconBg: "bg-emerald-100 dark:bg-emerald-900/30",
    iconColor: "text-emerald-600 dark:text-emerald-400",
  },
  {
    to: "/filters",
    label: "高级筛选器",
    desc: "配置筛选规则",
    icon: FunnelIcon,
    iconBg: "bg-amber-100 dark:bg-amber-900/30",
    iconColor: "text-amber-600 dark:text-amber-400",
  },
  {
    to: "/libraries",
    label: "媒体库管理",
    desc: "真实库同步与封面",
    icon: BuildingLibraryIcon,
    iconBg: "bg-purple-100 dark:bg-purple-900/30",
    iconColor: "text-purple-600 dark:text-purple-400",
  },
];

onMounted(() => {
  if (!store.dataStatus) store.fetchAllInitialData();
});
</script>
