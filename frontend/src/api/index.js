// frontend/src/api/index.js (Corrected and Cleaned)

import axios from 'axios';

const apiClient = axios.create({
    baseURL: '/api', 
});

export default {
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

    // 新增: Cover Generator
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
