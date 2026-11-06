<template>
  <div class="page page-wide">
    <h2>审计日志中心</h2>
    <p class="dim page-note">
      发起方与青协的所有增删改均留痕，对志愿者可见的记录同步展示在活动详情页。
      默认显示最近 7 天，一次最多查 {{ maxDays }} 天。
    </p>
    <el-card shadow="never">
      <div class="toolbar">
        <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD" size="small"
                        start-placeholder="开始日期" end-placeholder="结束日期"
                        style="width:240px;flex:none"
                        @change="reload" />
        <el-select v-model="filter.target_type" clearable placeholder="对象类型" style="width:150px" @change="load">
          <el-option value="activity" label="活动" />
          <el-option value="group" label="场次" />
          <el-option value="position" label="岗位" />
          <el-option value="assignment" label="岗位分配" />
          <el-option value="enrollment" label="报名记录" />
          <el-option value="user" label="用户/档案" />
          <el-option value="export" label="导出" />
          <el-option value="hours" label="时长" />
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
          <AuditChanges :changes="row.changes" strong />
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
            <AuditChanges :changes="row.changes" />
          </template>
        </el-table-column>
        <el-table-column label="时间" width="180">
          <template #default="{row}">{{ fmtDT(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <Pager :page="page" :page-size="20" :total="total" @change="goPage" />
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import AuditChanges from '../components/AuditChanges.vue'
import DataCards from '../components/DataCards.vue'
import Pager from '../components/Pager.vue'

const narrow = useNarrow()
const items = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const filter = ref({ target_type: '' })
const onlyVisible = ref(false)
const maxDays = ref(31)

/** 默认最近 7 天：留痕只增不减，不限时间时每翻一页都要数、排整张表。 */
function lastNDays(n) {
  const fmt = x => [x.getFullYear(),
                    String(x.getMonth() + 1).padStart(2, '0'),
                    String(x.getDate()).padStart(2, '0')].join('-')
  const t = new Date()
  const from = new Date(t.getFullYear(), t.getMonth(), t.getDate() - (n - 1))
  return [fmt(from), fmt(t)]
}
const range = ref(lastNDays(7))

async function load() {
  loading.value = true
  try {
    const q = new URLSearchParams({ page: page.value, size: 20 })
    const [start, end] = range.value || []
    if (start) q.set('start', start)
    if (end) q.set('end', end)
    if (filter.value.target_type) q.set('target_type', filter.value.target_type)
    if (onlyVisible.value) q.set('visible', 1)
    const d = await api.get(`/api/staff/audit-logs?${q}`)
    items.value = d.items
    total.value = d.total
    maxDays.value = d.max_days
  } finally { loading.value = false }
}
onMounted(load)

function reload() {
  page.value = 1
  load()
}

function goPage(p) {
  page.value = p
  load()
}
</script>

<style scoped>
</style>
