<template>
    <el-card shadow="never">
        <template #header>
            <div class="card-header">
                <span class="card-title">虚拟媒体库管理</span>
                <div class="header-actions">
                    <el-tooltip content="重启整个服务容器。这是清除代理内存缓存的正确方式。" placement="top">
                        <el-button
                            type="warning"
                            :icon="Refresh"
                            @click="store.restartProxyServer()"
                            plain
                        >
                            重启服务 (清缓存)
                        </el-button>
                    </el-tooltip>
                    <el-button @click="store.refreshAllCovers()">刷新所有封面</el-button>
                    <el-button @click="store.fetchAllEmbyData" :loading="store.dataLoading" :disabled="store.dataLoading">
                        {{ store.dataLoading ? '正在加载...' : '刷新Emby数据' }}
                    </el-button>
                    <el-button @click="store.openLayoutManager">调整主页布局</el-button>
                    <el-button type="primary" @click="store.openAddDialog">添加虚拟库</el-button>
                </div>
            </div>
        </template>

        <!-- Desktop table view -->
        <div class="table-view">
            <el-table :data="store.virtualLibraries" style="width: 100%" v-loading="store.dataLoading" :row-class-name="tableRowClassName">
                <el-table-column prop="name" label="虚拟库名称" min-width="160">
                    <template #default="scope">
                        <span :class="{ 'hidden-lib-name': scope.row.hidden }">{{ scope.row.name }}</span>
                        <el-tag v-if="scope.row.hidden" size="small" type="info" round style="margin-left: 6px;">已隐藏</el-tag>
                    </template>
                </el-table-column>
                <el-table-column label="封面" width="280" align="center" class-name="cover-col">
                    <template #default="scope">
                        <img v-if="scope.row.image_tag" class="cover-thumb" :src="`/covers/${scope.row.id}.jpg?t=${scope.row.image_tag}`" alt="封面" />
                        <span v-else class="cover-empty">—</span>
                    </template>
                </el-table-column>
                <el-table-column label="资源类型" width="120">
                    <template #default="scope">
                        {{ getResourceTypeLabel(scope.row.resource_type) }}
                    </template>
                </el-table-column>
                <el-table-column label="资源详情" min-width="200">
                    <template #default="scope">
                        {{ getResourceNameById(scope.row.resource_type, scope.row.resource_id, scope.row) }}
                    </template>
                </el-table-column>
                <el-table-column label="TMDB合并" width="100" align="center">
                    <template #default="scope">
                         <el-tag v-if="scope.row.merge_by_tmdb_id" type="success" size="small" round>已开启</el-tag>
                         <el-tag v-else type="info" size="small" round>未开启</el-tag>
                    </template>
                </el-table-column>
                <el-table-column label="源库限定" width="60" align="center">
                    <template #default="scope">
                         <el-tag v-if="scope.row.source_libraries && scope.row.source_libraries.length" type="warning" size="small" round>{{ scope.row.source_libraries.length }}个库</el-tag>
                         <el-tag v-else type="info" size="small" round>全部</el-tag>
                    </template>
                </el-table-column>
                <el-table-column label="操作" width="420" align="right">
                    <template #default="scope">
                        <div class="action-buttons">
                            <el-button v-if="scope.row.resource_type === 'rsshub'" size="small" type="success" @click="store.refreshRssLibrary(scope.row.id)">刷新RSS</el-button>
                            <el-button size="small" type="primary" plain @click="store.refreshLibraryCover(scope.row.id)">更新封面</el-button>
                            <el-button v-if="scope.row.resource_type !== 'rsshub'" size="small" type="success" plain @click="store.refreshLibraryData(scope.row.id)">更新数据</el-button>
                            <el-button size="small" @click="store.openEditDialog(scope.row)">编辑</el-button>
                            <el-button size="small" :type="scope.row.hidden ? 'warning' : 'info'" plain @click="store.toggleLibraryHidden(scope.row.id)">
                                {{ scope.row.hidden ? '显示' : '隐藏' }}
                            </el-button>
                            <el-popconfirm
                                :title="`确定要删除虚拟库 '${scope.row.name}' 吗？`"
                                width="250"
                                confirm-button-text="狠心删除"
                                cancel-button-text="我再想想"
                                :icon="WarningFilled"
                                icon-color="#F56C6C"
                                @confirm="store.deleteLibrary(scope.row.id)"
                            >
                                <template #reference>
                                    <el-button size="small" type="danger">删除</el-button>
                                </template>
                            </el-popconfirm>
                        </div>
                    </template>
                </el-table-column>
            </el-table>
        </div>

        <!-- Mobile card view -->
        <div class="card-view" v-loading="store.dataLoading">
            <div v-for="row in store.virtualLibraries" :key="row.id" class="lib-card" :class="{ 'lib-card-hidden': row.hidden }">
                <div class="lib-card-top">
                    <img v-if="row.image_tag" class="cover-thumb-card" :src="`/covers/${row.id}.jpg?t=${row.image_tag}`" alt="封面" />
                    <div class="lib-card-info">
                        <div class="lib-card-header">
                            <span class="lib-card-name">{{ row.name }}</span>
                            <div style="display: flex; gap: 4px; align-items: center;">
                                <el-tag v-if="row.hidden" size="small" type="info" round>已隐藏</el-tag>
                                <el-tag size="small" round>{{ getResourceTypeLabel(row.resource_type) }}</el-tag>
                            </div>
                        </div>
                        <div class="lib-card-detail">
                            {{ getResourceNameById(row.resource_type, row.resource_id, row) }}
                        </div>
                    </div>
                </div>
                <div class="lib-card-tags">
                    <el-tag v-if="row.merge_by_tmdb_id" type="success" size="small" round>TMDB合并</el-tag>
                    <el-tag v-if="row.source_libraries && row.source_libraries.length" type="warning" size="small" round>{{ row.source_libraries.length }}个源库</el-tag>
                </div>
                <div class="lib-card-actions">
                    <el-button v-if="row.resource_type === 'rsshub'" size="small" type="success" @click="store.refreshRssLibrary(row.id)">刷新RSS</el-button>
                    <el-button size="small" type="primary" plain @click="store.refreshLibraryCover(row.id)">封面</el-button>
                    <el-button v-if="row.resource_type !== 'rsshub'" size="small" type="success" plain @click="store.refreshLibraryData(row.id)">数据</el-button>
                    <el-button size="small" @click="store.openEditDialog(row)">编辑</el-button>
                    <el-button size="small" :type="row.hidden ? 'warning' : 'info'" plain @click="store.toggleLibraryHidden(row.id)">
                        {{ row.hidden ? '显示' : '隐藏' }}
                    </el-button>
                    <el-popconfirm
                        :title="`确定要删除虚拟库 '${row.name}' 吗？`"
                        width="250"
                        confirm-button-text="狠心删除"
                        cancel-button-text="我再想想"
                        :icon="WarningFilled"
                        icon-color="#F56C6C"
                        @confirm="store.deleteLibrary(row.id)"
                    >
                        <template #reference>
                            <el-button size="small" type="danger">删除</el-button>
                        </template>
                    </el-popconfirm>
                </div>
            </div>
            <el-empty v-if="!store.dataLoading && (!store.virtualLibraries || store.virtualLibraries.length === 0)" description="暂无虚拟库" />
        </div>
    </el-card>
</template>

<script setup>
import { useMainStore } from '@/stores/main';
import { WarningFilled, Refresh } from '@element-plus/icons-vue';

const store = useMainStore();

const tableRowClassName = ({ row }) => {
    return row.hidden ? 'hidden-row' : '';
};

const resourceTypeMap = {
    collection: '合集',
    tag: '标签',
    genre: '类型',
    studio: '工作室',
    person: '人员',
    rsshub: 'RSSHUB',
    random: '随机推荐',
    all: '全库'
};

const getResourceTypeLabel = (type) => resourceTypeMap[type] || '未知';

const getResourceNameById = (type, id, row) => {
    if (type === 'rsshub') {
        const custom = Number(row.cache_refresh_interval);
        const refreshText = Number.isFinite(custom) && custom > 0 ? `${custom}h` : '跟随全局';
        return `${row.rsshub_url}（刷新: ${refreshText}）`;
    }
    if (type === 'random') return '基于播放记录推荐';
    if (type === 'all') return '全部媒体库';

    const refreshSuffix = (() => {
        const custom = Number(row.cache_refresh_interval);
        return Number.isFinite(custom) && custom > 0 ? `（刷新: ${custom}h）` : '';
    })();

    if (type === 'person') {
        const name = store.personNameCache[id];
        if (name && name !== '...') return `${name} (${id})${refreshSuffix}`;
        return name === '...' ? '正在加载...' : `ID: ${id}`;
    }
    const pluralType = type + 's';
    const resourceList = store.classifications[pluralType] || [];
    const resource = resourceList.find(r => r.id === id);
    return resource ? `${resource.name} (${resource.id})${refreshSuffix}` : `未知ID: ${id}`;
};
</script>

<style scoped>
.card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
}

.card-title {
    font-size: 16px;
    font-weight: 600;
}

.header-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.action-buttons {
    display: flex;
    justify-content: flex-end;
    gap: 4px;
    flex-wrap: wrap;
}

/* Mobile card view - hidden by default */
.card-view {
    display: none;
}

.lib-card {
    padding: 16px;
    border-radius: 10px;
    background: var(--el-fill-color-lighter);
    margin-bottom: 12px;
    transition: background var(--transition-fast);
}

.lib-card:hover {
    background: var(--el-fill-color-light);
}

.lib-card-hidden {
    opacity: 0.55;
}

.hidden-lib-name {
    opacity: 0.5;
}

/* Cover thumbnail */
.cover-thumb {
    width: 48px;
    height: 48px;
    object-fit: cover;
    border-radius: 6px;
    border: 1px solid var(--el-border-color-lighter);
}
.cover-empty {
    color: var(--el-text-color-placeholder);
    font-size: 18px;
}
.lib-card-top {
    display: flex;
    gap: 12px;
    margin-bottom: 10px;
}
.cover-thumb-card {
    width: 56px;
    height: 56px;
    object-fit: cover;
    border-radius: 6px;
    border: 1px solid var(--el-border-color-lighter);
    flex-shrink: 0;
}
.lib-card-info {
    flex: 1;
    min-width: 0;
}

:deep(.hidden-row) {
    opacity: 0.55;
}

.lib-card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
}

.lib-card-name {
    font-weight: 600;
    font-size: 15px;
    color: var(--el-text-color-primary);
}

.lib-card-detail {
    font-size: 13px;
    color: var(--el-text-color-secondary);
    margin-bottom: 10px;
    word-break: break-all;
}

.lib-card-tags {
    display: flex;
    gap: 3px;
    margin-bottom: 12px;
    flex-wrap: wrap;
}

.lib-card-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 3px;
}

/* Responsive */
@media (max-width: 900px) {
    .table-view {
        display: none;
    }

    .card-view {
        display: block;
    }

    .card-header {
        flex-direction: column;
        align-items: flex-start;
    }

    .header-actions {
        width: 100%;
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
    }

    .header-actions .el-button {
        margin-left: 0 !important;
    }

    /* 让"添加虚拟库"独占一行突出显示 */
    .header-actions .el-button:last-child {
        grid-column: 1 / -1;
    }
}
</style>
