<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-2xl font-bold text-gray-900 dark:text-gray-100">
          核心设置
        </h1>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
          Emby连接、缓存、Webhook
        </p>
      </div>
      <BaseButton
        variant="primary"
        :loading="store.saving"
        @click="store.saveConfig()"
        >保存所有设置</BaseButton
      >
    </div>

    <div v-if="store.config" class="space-y-6">
      <!-- Connection -->
      <section
        class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5"
      >
        <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-4">
          服务器连接
        </h2>
        <div class="space-y-4">
          <BaseInput
            v-model="store.config.emby_url"
            label="Emby 服务器地址"
            placeholder="http://192.168.1.10:8096"
            hint="如果使用了302，请填写302地址。"
          />
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <BaseInput
              v-model="store.config.emby_api_key"
              label="Emby API 密钥"
              type="password"
              placeholder="API Key"
              hint="在 Emby 后台 → API 密钥中生成。"
            />
            <BaseInput
              v-model="store.config.tmdb_api_key"
              label="TMDB API 密钥"
              type="password"
              placeholder="TMDB API Key"
              hint="用于获取缺失剧集信息。"
            />
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <BaseInput
              v-model="store.config.tmdb_proxy"
              label="TMDB HTTP 代理"
              placeholder="http://127.0.0.1:7890"
              hint="无法直接访问 TMDB 时使用。"
            />
            <div>
              <label
                class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
                >缓存刷新间隔（小时）</label
              >
              <input
                type="number"
                v-model.number="store.config.cache_refresh_interval"
                min="0"
                class="block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 focus:outline-none"
              />
              <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                RSS 定时刷新和虚拟库缓存 TTL 默认值。0 表示仅手动/Webhook 刷新。
              </p>
            </div>
          </div>
        </div>
      </section>

      <!-- Switches -->
      <section
        class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5"
      >
        <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-4">
          功能开关
        </h2>
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <SwitchCard
            label="启用内存缓存"
            desc="开启后代理会缓存部分 Emby JSON 响应，减轻 Emby 压力。关闭后直连 Emby、不占用该项内存缓存。"
            v-model="store.config.enable_cache"
          />
          <SwitchCard
            label="显示缺失剧集"
            desc="从 TMDB 查询并显示本地缺失的剧集。"
            v-model="store.config.show_missing_episodes"
          />
          <SwitchCard
            label="全局强制 TMDB 合并"
            desc="无视虚拟库独立设置，强制合并。"
            v-model="store.config.force_merge_by_tmdb_id"
          />
        </div>
      </section>

      <!-- Cover -->
      <section
        class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5"
      >
        <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-4">
          封面生成
        </h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <BaseSelect
            v-model="store.config.default_cover_style"
            :options="coverStyleOptions"
            label="默认封面样式"
            hint="自动生成封面时使用的样式。"
          />
          <BaseInput
            v-model="store.config.custom_zh_font_path"
            label="自定义中文字体路径"
            placeholder="/config/fonts/myfont.ttf"
            hint="留空使用默认字体。"
          />
          <BaseInput
            v-model="store.config.custom_en_font_path"
            label="自定义英文字体路径"
            placeholder="/config/fonts/myfont.otf"
            hint="留空使用默认字体。"
          />
          <BaseInput
            v-model="store.config.custom_image_path"
            label="全局自定义图片目录"
            placeholder="/config/images/custom"
            hint="留空则从虚拟库内容下载封面。"
          />
        </div>
      </section>

      <!-- Hide types -->
      <section
        class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5"
      >
        <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-4">
          全局隐藏类型
        </h2>
        <div class="flex flex-wrap gap-2 mb-3">
          <button
            v-for="ct in collectionTypes"
            :key="ct.value"
            class="px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors"
            :class="
              isHidden(ct.value)
                ? 'bg-primary-100 text-primary-700 border-primary-300 dark:bg-primary-900/40 dark:text-primary-300 dark:border-primary-700'
                : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50 dark:bg-gray-800 dark:text-gray-400 dark:border-gray-600 dark:hover:bg-gray-700'
            "
            @click="toggleHide(ct.value)"
          >
            {{ ct.label }}
          </button>
        </div>
        <p class="text-xs text-gray-500 dark:text-gray-400">
          选中的类型将在主页视图中被隐藏。
        </p>
      </section>

      <!-- Webhook -->
      <section
        class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700 p-5"
      >
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100">
            Emby Webhook
          </h2>
          <BaseSwitch v-model="store.config.webhook.enabled" />
        </div>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">
          库变更后自动刷新关联的虚拟库。仅对「全部媒体库」类型且做了源库限定的虚拟库生效。
        </p>
        <template v-if="store.config.webhook.enabled">
          <div class="space-y-4">
            <BaseInput
              :model-value="webhookCallbackUrl"
              label="回调 URL（POST）"
              readonly
              hint="将此 URL 填入 Emby Premiere Webhook 目标。"
            />
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <BaseInput
                v-model="store.config.webhook.secret"
                label="密钥（可选）"
                type="password"
                placeholder="留空则不校验"
              />
              <div>
                <label
                  class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1"
                  >延迟刷新（秒）</label
                >
                <input
                  type="number"
                  v-model.number="store.config.webhook.delay_seconds"
                  min="0"
                  max="3600"
                  step="30"
                  class="block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 focus:border-primary-500 focus:ring-2 focus:ring-primary-500/20 focus:outline-none"
                />
                <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  0 表示立即刷新。延迟期间多次通知会合并。
                </p>
              </div>
            </div>
          </div>
        </template>
      </section>

      <!-- Danger zone -->
      <section
        class="rounded-xl border border-dashed border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-5"
      >
        <h2 class="text-sm font-semibold text-red-700 dark:text-red-400 mb-3">
          危险区域
        </h2>
        <div class="flex flex-wrap gap-3">
          <ConfirmPopover
            message="确定要清空所有本地生成的封面吗？此操作不可逆。"
            confirm-text="确定清空"
            @confirm="store.clearAllCovers()"
          >
            <template #trigger>
              <BaseButton variant="danger-outline" :loading="store.saving"
                >清空所有本地封面</BaseButton
              >
            </template>
          </ConfirmPopover>
          <ConfirmPopover
            message="确定要重启代理服务容器吗？"
            confirm-text="确定重启"
            @confirm="store.restartProxyServer()"
          >
            <template #trigger>
              <BaseButton variant="warning" :loading="store.saving"
                >重启代理服务</BaseButton
              >
            </template>
          </ConfirmPopover>
        </div>
        <p class="text-xs text-red-600/70 dark:text-red-400/70 mt-2">
          清空封面将删除所有生成的图片并重置封面状态。重启服务将清除代理内存缓存。
        </p>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from "vue";
import { useMainStore } from "@/stores/main";
import BaseInput from "@/components/ui/BaseInput.vue";
import BaseSelect from "@/components/ui/BaseSelect.vue";
import BaseButton from "@/components/ui/BaseButton.vue";
import BaseSwitch from "@/components/ui/BaseSwitch.vue";
import ConfirmPopover from "@/components/ui/ConfirmPopover.vue";
import SwitchCard from "@/components/SwitchCard.vue";

const store = useMainStore();

const coverStyleOptions = [
  { value: "style_multi_1", label: "样式一 (多图)" },
  { value: "style_single_1", label: "样式二 (单图)" },
  { value: "style_single_2", label: "样式三 (单图)" },
];

const webhookCallbackUrl = computed(() =>
  typeof window !== "undefined"
    ? `${window.location.origin}/api/webhook/emby`
    : "",
);

const collectionTypes = [
  { value: "movies", label: "电影" },
  { value: "tvshows", label: "电视剧" },
  { value: "music", label: "音乐" },
  { value: "playlists", label: "播放列表" },
  { value: "musicvideos", label: "音乐视频" },
  { value: "livetv", label: "电视直播" },
  { value: "boxsets", label: "合集" },
  { value: "photos", label: "照片" },
  { value: "homevideos", label: "家庭视频" },
  { value: "books", label: "书籍" },
];

const isHidden = (val) => (store.config.hide || []).includes(val);
const toggleHide = (val) => {
  if (!store.config.hide) store.config.hide = [];
  const idx = store.config.hide.indexOf(val);
  if (idx === -1) store.config.hide.push(val);
  else store.config.hide.splice(idx, 1);
};

onMounted(() => {
  if (!store.dataStatus) store.fetchAllInitialData();
});
</script>
