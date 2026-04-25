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
            v-if="store.activeServer"
            v-model="store.activeServer.emby_url"
            label="Emby 服务器地址"
            placeholder="http://192.168.1.10:8096"
            hint="如果使用了302，请填写302地址。"
          />
          <div
            v-if="store.activeServer"
            class="grid grid-cols-1 md:grid-cols-2 gap-4"
          >
            <BaseInput
              v-model="store.activeServer.emby_api_key"
              label="Emby API 密钥"
              type="password"
              placeholder="API Key"
              hint="在 Emby 后台 → API 密钥中生成。"
            />
            <BaseInput
              v-model="store.activeServer.proxy_port"
              label="代理端口（对外访问）"
              type="number"
              placeholder="8999"
              hint='需同时在 docker-compose.yml 中手动映射该端口（例如 "9000:9000"），否则外部客户端无法访问。保存后需重启生效。'
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
            :model-value="store.config.default_cover_style"
            @update:modelValue="(v) => (store.config.default_cover_style = v)"
            :options="coverStyleOptions"
            label="默认封面样式"
            hint="自动生成封面时使用的样式。"
          />
          <BaseSelect
            :model-value="store.config.cover_style_variant"
            @update:modelValue="(v) => (store.config.cover_style_variant = v)"
            :options="coverVariantOptions"
            label="封面类型"
            hint="可选静态图、动态 GIF 或动态 APNG。"
          />
          <BaseInput
            v-if="isAnimatedVariant"
            v-model.number="store.config.animation_duration"
            label="动图时长（秒）"
            type="number"
            min="2"
            max="30"
          />
          <BaseInput
            v-if="isAnimatedVariant"
            v-model.number="store.config.animation_fps"
            label="动图帧率（FPS）"
            type="number"
            min="6"
            max="24"
          />
          <BaseInput
            v-if="
              isAnimatedVariant &&
              ['style_single_1', 'style_single_2'].includes(
                store.config.default_cover_style,
              )
            "
            v-model.number="store.config.animated_image_count"
            label="样式2/3图片数量"
            type="number"
            min="3"
            max="9"
          />
          <BaseSelect
            v-if="
              isAnimatedVariant &&
              store.config.default_cover_style === 'style_single_1'
            "
            v-model="store.config.animated_departure_type"
            :options="animatedDepartureOptions"
            label="样式1动画风格"
          />
          <BaseSelect
            v-if="
              isAnimatedVariant &&
              store.config.default_cover_style === 'style_multi_1'
            "
            v-model="store.config.animated_scroll_direction"
            :options="animatedScrollDirectionOptions"
            label="样式1滚动方向"
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
          每个 Emby 服务器独立 Webhook 配置与 token。token 为必填且全局不可重复，事件只作用于该 token 对应服务器。
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
                label="Webhook Token"
                type="password"
                required
                placeholder="必填，且与其它服务器不能重复"
                hint="支持从 X-Webhook-Secret、Authorization: Bearer 或 ?token= 读取。"
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
            <p
              v-if="webhookTokenError"
              class="text-xs text-red-600 dark:text-red-400"
            >
              {{ webhookTokenError }}
            </p>
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
import previewMulti1 from "@/images/style_preview_style_multi_1.jpg";
import previewSingle1 from "@/images/style_preview_style_single_1.jpg";
import previewSingle2 from "@/images/style_preview_style_single_2.jpg";
import previewShelf1 from "@/images/style_preview_style_shelf_1.jpg";

const store = useMainStore();

const coverStyleOptions = [
  {
    value: "style_multi_1",
    label: "样式一 (多图)",
    preview: previewMulti1,
  },
  {
    value: "style_single_1",
    label: "样式二 (单图)",
    preview: previewSingle1,
  },
  {
    value: "style_single_2",
    label: "样式三 (单图)",
    preview: previewSingle2,
  },
  {
    value: "style_shelf_1",
    label: "样式四 (背景+底栏海报)",
    preview: previewShelf1,
  },
];

const coverVariantOptions = [
  { value: "static", label: "静态图" },
  { value: "animated", label: "动态 GIF" },
  { value: "animated_apng", label: "动态 APNG" },
];

const isAnimatedVariant = computed(() =>
  ["animated", "animated_apng"].includes(store.config.cover_style_variant),
);

const animatedDepartureOptions = [
  { value: "fly", label: "旋转-飞出" },
  { value: "fade", label: "淡入淡出" },
  { value: "crossfade", label: "交叠过渡" },
];

const animatedScrollDirectionOptions = [
  { value: "up", label: "向上滚动" },
  { value: "down", label: "向下滚动" },
  { value: "alternate", label: "交替（两边上/中间下）" },
  { value: "alternate_reverse", label: "交替反向（两边下/中间上）" },
];

const webhookCallbackUrl = computed(() =>
  typeof window !== "undefined"
    ? `${window.location.origin}/api/webhook/emby`
    : "",
);

const webhookTokenError = computed(() => {
  if (!store.config?.webhook?.enabled) return "";
  const active = store.activeServer;
  const token = String(store.config?.webhook?.secret || "").trim();
  if (!token) return "当前服务器已启用 Webhook，Token 必填。";
  const duplicate = (store.config?.servers || []).some((s) => {
    if (String(s?.id) === String(active?.id)) return false;
    const p = s?.profile || {};
    const w = p?.webhook || {};
    if (w?.enabled !== true) return false;
    return String(w?.secret || "").trim() === token;
  });
  if (duplicate) return "Token 与其它已启用服务器重复，请更换。";
  return "";
});

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
