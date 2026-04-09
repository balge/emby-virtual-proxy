// frontend/src/api/index.js

import axios from "axios";

const apiClient = axios.create({
  baseURL: "/api",
});

// 请求拦截器：自动附加 auth token
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem("auth_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截器：401 时清除 token 并跳转登录
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      localStorage.removeItem("auth_token");
      // 触发自定义事件通知 App 切换到登录页
      window.dispatchEvent(new Event("auth-expired"));
    }
    return Promise.reject(error);
  },
);

export default {
  // Auth
  getAuthStatus: () => apiClient.get("/auth-status"),
  login: (username, password) =>
    apiClient.post("/login", { username, password }),
  logout: () => apiClient.post("/logout"),

  // System
  getConfig: () => apiClient.get("/config"),
  updateConfig: (config) => apiClient.post("/config", config),
  getServerProfile: (id) => apiClient.get(`/servers/${id}/profile`),
  updateServerProfile: (id, profile) => apiClient.put(`/servers/${id}/profile`, profile),
  restartProxy: () => apiClient.post("/proxy/restart"),

  // Libraries
  addLibrary: (library) => apiClient.post("/libraries", library),
  updateLibrary: (id, library) => apiClient.put(`/libraries/${id}`, library),
  deleteLibrary: (id) => apiClient.delete(`/libraries/${id}`),
  toggleLibraryHidden: (id) =>
    apiClient.patch(`/libraries/${id}/toggle-hidden`),
  refreshRssLibrary: (id) => apiClient.post(`/libraries/${id}/refresh`),
  refreshLibraryCover: (id) => apiClient.post(`/libraries/${id}/refresh-cover`),
  refreshLibraryData: (id) => apiClient.post(`/libraries/${id}/refresh-data`),

  // Display Management
  getAllLibraries: () => apiClient.get("/all-libraries"),
  saveDisplayOrder: (orderedIds) =>
    apiClient.post("/display-order", orderedIds),

  // Emby Helpers
  getClassifications: () => apiClient.get("/emby/classifications"),
  /** 不传 query 或空字符串时由后端返回 /Persons 分页列表（用于无关键词浏览） */
  searchPersons: (query, page = 1) => {
    const params = { page };
    if (query != null && String(query).trim() !== "")
      params.query = String(query).trim();
    return apiClient.get("/emby/persons/search", { params });
  },
  resolveItem: (itemId) => apiClient.get(`/emby/resolve-item/${itemId}`),

  // Advanced Filters
  getAdvancedFilters: () => apiClient.get("/advanced-filters"),
  saveAdvancedFilters: (filters) =>
    apiClient.post("/advanced-filters", filters),

  // Real Libraries
  syncRealLibraries: () => apiClient.get("/real-libraries/sync"),
  saveRealLibraries: (libraries, coverCron) =>
    apiClient.post("/real-libraries", { libraries, cover_cron: coverCron }),
  refreshRealLibraryCover: (id) =>
    apiClient.post(`/real-libraries/${id}/refresh-cover`),
  refreshAllRealLibraryCovers: () =>
    apiClient.post("/real-libraries/refresh-all-covers"),

  // Cover Generator
  generateCover: (libraryId, titleZh, titleEn, styleName, tempImagePaths) =>
    apiClient.post("/generate-cover", {
      library_id: libraryId,
      title_zh: titleZh,
      title_en: titleEn,
      style_name: styleName,
      temp_image_paths: tempImagePaths,
    }),
  clearCovers: () => apiClient.post("/covers/clear"),
  refreshAllCovers: () => apiClient.post("/covers/refresh-all"),
};
