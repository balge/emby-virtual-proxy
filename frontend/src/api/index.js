// frontend/src/api/index.js

import axios from 'axios';

const apiClient = axios.create({
    baseURL: '/api', 
});

// 请求拦截器：自动附加 auth token
apiClient.interceptors.request.use((config) => {
    const token = localStorage.getItem('auth_token');
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
            localStorage.removeItem('auth_token');
            // 触发自定义事件通知 App 切换到登录页
            window.dispatchEvent(new Event('auth-expired'));
        }
        return Promise.reject(error);
    }
);

export default {
    // Auth
    getAuthStatus: () => apiClient.get('/auth-status'),
    login: (username, password) => apiClient.post('/login', { username, password }),
    logout: () => apiClient.post('/logout'),

    // System
    getConfig: () => apiClient.get('/config'),
    updateConfig: (config) => apiClient.post('/config', config),
    restartProxy: () => apiClient.post('/proxy/restart'),

    // Libraries
    addLibrary: (library) => apiClient.post('/libraries', library),
    updateLibrary: (id, library) => apiClient.put(`/libraries/${id}`, library),
    deleteLibrary: (id) => apiClient.delete(`/libraries/${id}`),
    refreshRssLibrary: (id) => apiClient.post(`/libraries/${id}/refresh`),
    refreshLibraryCover: (id) => apiClient.post(`/libraries/${id}/refresh-cover`),
    refreshLibraryData: (id) => apiClient.post(`/libraries/${id}/refresh-data`),

    // Display Management
    getAllLibraries: () => apiClient.get('/all-libraries'),
    saveDisplayOrder: (orderedIds) => apiClient.post('/display-order', orderedIds),

    // Emby Helpers
    getClassifications: () => apiClient.get('/emby/classifications'),
    searchPersons: (query, page = 1) => apiClient.get('/emby/persons/search', { params: { query, page } }),
    resolveItem: (itemId) => apiClient.get(`/emby/resolve-item/${itemId}`),

    // Advanced Filters
    getAdvancedFilters: () => apiClient.get('/advanced-filters'),
    saveAdvancedFilters: (filters) => apiClient.post('/advanced-filters', filters),

    // Cover Generator
    generateCover: (libraryId, titleZh, titleEn, styleName, tempImagePaths) => apiClient.post('/generate-cover', {
        library_id: libraryId,
        title_zh: titleZh,
        title_en: titleEn,
        style_name: styleName,
        temp_image_paths: tempImagePaths
    }),
    clearCovers: () => apiClient.post('/covers/clear'),
    refreshAllCovers: () => apiClient.post('/covers/refresh-all'),
};
