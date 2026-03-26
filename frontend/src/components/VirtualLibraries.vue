<template>
    <el-card shadow="never">
        <template #header>
            <div class="card-header">
                <span>虚拟媒体库管理</span>
                <div>
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

        <el-table :data="store.virtualLibraries" style="width: 100%" v-loading="store.dataLoading">
            <el-table-column prop="name" label="虚拟库名称" width="200" />
            <el-table-column label="资源类型" width="180">
                <template #default="scope">
                    {{ getResourceTypeLabel(scope.row.resource_type) }}
                </template>
            </el-table-column>
            <el-table-column label="资源详情">
                <template #default="scope">
                    {{ getResourceNameById(scope.row.resource_type, scope.row.resource_id, scope.row) }}
                </template>
            </el-table-column>
            <el-table-column label="TMDB合并" width="100" align="center">
                <template #default="scope">
                     <el-tag v-if="scope.row.merge_by_tmdb_id" type="success" size="small">已开启</el-tag>
                     <el-tag v-else type="info" size="small">未开启</el-tag>
                </template>
            </el-table-column>
            <el-table-column label="源库限定" width="100" align="center">
                <template #default="scope">
                     <el-tag v-if="scope.row.source_libraries && scope.row.source_libraries.length" type="warning" size="small">{{ scope.row.source_libraries.length }}个库</el-tag>
                     <el-tag v-else type="info" size="small">全部</el-tag>
                </template>
            </el-table-column>
            <el-table-column label="操作" width="340" align="right">
                <template #default="scope">
                    <div class="action-buttons">
                        <el-button v-if="scope.row.resource_type === 'rsshub'" size="small" type="success" @click="store.refreshRssLibrary(scope.row.id)">刷新RSS</el-button>
                        <el-button size="small" type="primary" plain @click="store.refreshLibraryCover(scope.row.id)">更新封面</el-button>
                        <el-button size="small" type="success" plain @click="store.refreshLibraryData(scope.row.id)">更新数据</el-button>
                        <el-button size="small" @click="store.openEditDialog(scope.row)">编辑</el-button>
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
    </el-card>
</template>

<script setup>
import { useMainStore } from '@/stores/main';
import { WarningFilled, Refresh } from '@element-plus/icons-vue';

const store = useMainStore();

const resourceTypeMap = {
    collection: '合集',
    tag: '标签',
    genre: '类型',
    studio: '工作室',
    person: '人员',
    rsshub: 'RSSHUB',
    random: '随机推荐'
};

const getResourceTypeLabel = (type) => resourceTypeMap[type] || '未知';

const getResourceNameById = (type, id, row) => {
    if (type === 'rsshub') return row.rsshub_url;
    if (type === 'random') return '基于播放记录推荐';
    if (type === 'all') return '全部媒体库';
    if (type === 'person') {
        const name = store.personNameCache[id];
        if (name && name !== '...') return `${name} (${id})`;
        return name === '...' ? '正在加载...' : `ID: ${id}`;
    }
    const pluralType = type + 's';
    const resourceList = store.classifications[pluralType] || [];
    const resource = resourceList.find(r => r.id === id);
    return resource ? `${resource.name} (${resource.id})` : `未知ID: ${id}`;
};
</script>

<style scoped>
.card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
}
.action-buttons {
    display: flex;
    justify-content: flex-end;
    gap: 4px;
    flex-wrap: wrap;
}
</style>
