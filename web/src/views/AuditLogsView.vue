<template>
  <div class="page page-wide">
    <h2 style="margin-top:0">审计日志中心</h2>
    <p class="dim" style="margin-top:-6px">
      「谁动了我的岗位」——发起方/青协的所有增删改全量留痕；对志愿者可见的记录同步展示在活动详情页。
    </p>
    <el-card shadow="never">
      <div class="toolbar">
        <el-select v-model="filter.target_type" clearable placeholder="对象类型" style="width:150px" @change="load">
          <el-option value="activity" label="活动" />
          <el-option value="group" label="场次" />
          <el-option value="position" label="岗位" />
          <el-option value="assignment" label="岗位分配" />
          <el-option value="enrollment" label="报名记录" />
          <el-option value="user" label="用户/档案" />
          <el-option value="export" label="导出" />
          <el-option value="hours" label="时长" />
          <el-option value="integration" label="集成配置" />
        </el-select>
        <el-checkbox v-model="onlyVisible" @change="load">仅志愿者可见</el-checkbox>
        <span class="dim">共 {{ total }} 条</span>
      </div>
      <DataCards v-if="narrow" :rows="items" title-key="summary" v-loading="loading"
                 :fields="[{ key: 'actor_name', label: '操作人' },
                           { key: 'created_at', label: '时间', format: fmtDT }]"
                 empty="暂无记录">
        <template #extra="{ row }">
          <el-tag size="small" effect="plain">{{ row.action_label }}</el-tag>
        </template>
        <template #body="{ row }">
          <div v-for="(ch, i) in row.changes" :key="i" class="dim" style="line-height:1.7">
            {{ ch.label }}：{{ ch.old || '空' }} →
            <b style="color:var(--el-color-primary)">{{ ch.new || '空' }}</b>
          </div>
        </template>
      </DataCards>

      <el-table v-else :data="items" size="small" v-loading="loading">
        <el-table-column prop="actor_name" label="操作人" width="90" />
        <el-table-column label="动作" width="100">
          <template #default="{row}"><el-tag size="small" effect="plain">{{ row.action_label }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="target_label" label="对象" min-width="180" show-overflow-tooltip />
        <el-table-column prop="summary" label="摘要" min-width="300" show-overflow-tooltip />
        <el-table-column label="变更明细" min-width="240">
          <template #default="{row}">
            <div v-for="(ch, i) in row.changes" :key="i" class="dim">
              {{ ch.label }}：{{ ch.old || '空' }} → {{ ch.new || '空' }}
            </div>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="110">
          <template #default="{row}">{{ fmtDT(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <el-pagination layout="prev, pager, next" :total="total" :page-size="20"
                     v-model:current-page="page" @current-change="load"
                     style="margin-top:12px;justify-content:flex-end" />
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from '../components/DataCards.vue'

const narrow = useNarrow()
const items = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const filter = ref({ target_type: '' })
const onlyVisible = ref(false)

async function load() {
  loading.value = true
  try {
    const d = await api.get(`/api/staff/audit-logs?page=${page.value}&size=20`
      + (filter.value.target_type ? `&target_type=${filter.value.target_type}` : '')
      + (onlyVisible.value ? '&visible=1' : ''))
    items.value = d.items
    total.value = d.total
  } finally { loading.value = false }
}
onMounted(load)
</script>

<style scoped>
.toolbar { display:flex; gap:12px; align-items:center; margin-bottom:10px; }
</style>
