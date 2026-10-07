<template>
  <div class="page page-wide">
    <h2>操作日志</h2>
    <p class="dim" style="margin-top:-8px">
      所有改数据的操作，以及登录、导出和附件下载，都按请求记在这里，保留 {{ RETAIN_DAYS }} 天。
      提交的内容不记。一次最多查 {{ maxDays }} 天。
    </p>
    <el-card shadow="never">
      <div class="toolbar">
        <div class="when"><DateTimeField v-model="filter.start" placeholder="开始时间" /></div>
        <span class="dim">至</span>
        <div class="when"><DateTimeField v-model="filter.end" placeholder="结束时间（默认现在）" /></div>
        <el-input v-model="filter.person" clearable placeholder="学号或姓名" class="field"
                  @keyup.enter="reload" @clear="reload" />
        <el-input v-model="filter.activity" clearable placeholder="活动名称" class="field"
                  @keyup.enter="reload" @clear="reload" />
        <el-select v-model="filter.kind" clearable placeholder="操作类型" class="kind" @change="reload">
          <el-option v-for="(label, k) in kinds" :key="k" :value="k" :label="label" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
        <el-button @click="exportXlsx">导出 Excel</el-button>
        <span class="dim">共 {{ total }} 条</span>
      </div>

      <DataCards v-if="narrow" :rows="items" title-key="label" v-loading="loading"
                 :fields="[{ key: 'who', label: '操作人' },
                           { key: 't', label: '时间', format: fmtTime },
                           { key: 'activity_title', label: '活动' },
                           { key: 'path', label: '接口' },
                           { key: 'ip', label: 'IP' }]"
                 empty="暂无记录">
        <template #extra="{ row }">
          <el-tag size="small" effect="plain" :type="row.status >= 400 ? 'danger' : 'info'">
            {{ row.kind_label }} · {{ row.status }}
          </el-tag>
        </template>
      </DataCards>

      <el-table v-else :data="items" size="small" v-loading="loading">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.t) }}</template>
        </el-table-column>
        <el-table-column label="操作人" width="170">
          <template #default="{ row }">
            {{ row.who }}
            <div v-if="row.identity" class="dim">{{ ROLE_LABELS[row.identity] || row.identity }}视角</div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.kind_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="label" label="操作" min-width="160" show-overflow-tooltip />
        <el-table-column label="接口" min-width="260" show-overflow-tooltip>
          <template #default="{ row }"><span class="mono">{{ row.method }} {{ row.path }}</span></template>
        </el-table-column>
        <el-table-column prop="activity_title" label="活动" min-width="160" show-overflow-tooltip />
        <el-table-column label="结果" width="70">
          <template #default="{ row }">
            <span :class="{ bad: row.status >= 400 }">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="80">
          <template #default="{ row }">{{ row.ms }} ms</template>
        </el-table-column>
        <el-table-column prop="ip" label="IP" width="130" />
      </el-table>
      <el-pagination layout="total, prev, pager, next" :total="total" :page-size="SIZE"
                     v-model:current-page="page" @current-change="load"
                     style="margin-top:12px;justify-content:flex-end" />
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api, download } from '../api'
import { ROLE_LABELS } from '../store'
import { useNarrow } from '../composables/useNarrow'
import DataCards from '../components/DataCards.vue'
import DateTimeField from '../components/DateTimeField.vue'

const SIZE = 20
const RETAIN_DAYS = 180
const narrow = useNarrow()
const items = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const kinds = ref({})
const maxDays = ref(31)
const filter = ref({ start: '', end: '', person: '', activity: '', kind: '' })

const pad = n => String(n).padStart(2, '0')
// 后端认不带时区的本地时间串
function localText(v) {
  if (!v) return ''
  const d = v instanceof Date ? v : new Date(String(v).replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
       + `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
const fmtTime = t => (t ? t.replace('T', ' ').slice(0, 19) : '—')

function query() {
  const f = filter.value
  const q = new URLSearchParams()
  if (f.start) q.set('start', localText(f.start))
  if (f.end) q.set('end', localText(f.end))
  for (const k of ['person', 'activity', 'kind']) if (f[k].trim()) q.set(k, f[k].trim())
  return q
}

async function load() {
  loading.value = true
  try {
    const q = query()
    q.set('page', page.value)
    q.set('size', SIZE)
    const d = await api.get(`/api/staff/oplogs?${q}`)
    items.value = d.items.map(r => ({
      ...r, who: r.name ? `${r.name}（${r.sid}）` : (r.sid || '未登录'),
    }))
    total.value = d.total
    kinds.value = d.kinds
    maxDays.value = d.max_days
  } finally { loading.value = false }
}

function reload() {
  page.value = 1
  load()
}

function exportXlsx() {
  download(`/api/staff/oplogs.xlsx?${query()}`)
}

onMounted(load)
</script>

<style scoped>
.when { width: 200px; }
.field { width: 160px; }
.kind { width: 120px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.bad { color: var(--el-color-danger); }
@media (max-width: 860px) {
  .when, .field, .kind { width: 100%; }
}
</style>
