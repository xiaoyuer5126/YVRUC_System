<template>
  <div class="page page-wide">
    <h2>通知记录</h2>
    <p class="dim" style="margin:0 0 12px">
      每条通知发给了谁、是谁触发的，都记在这里。推送状态分成功、失败和仅站内
      （演示模式不往外推）。一次最多查 {{ maxDays }} 天。
    </p>
    <p class="today">
      今日发出 <b>{{ today.total }}</b> 条 · 推送失败
      <b :class="{ bad: today.failed }">{{ today.failed }}</b> 条
    </p>
    <el-card shadow="never">
      <div class="toolbar">
        <div class="when"><DateTimeField v-model="filter.start" placeholder="开始时间（默认最近 7 天）" /></div>
        <span class="dim">至</span>
        <div class="when"><DateTimeField v-model="filter.end" placeholder="结束时间（默认现在）" /></div>
        <el-input v-model="filter.person" clearable placeholder="收件人学号或姓名" class="field"
                  @keyup.enter="reload" @clear="reload" />
        <el-input v-model="filter.activity" clearable placeholder="活动名称" class="field"
                  @keyup.enter="reload" @clear="reload" />
        <el-select v-model="filter.template" clearable placeholder="类型" class="kind" @change="reload">
          <el-option v-for="(label, k) in templates" :key="k" :value="k" :label="label" />
        </el-select>
        <el-select v-model="filter.status" clearable placeholder="推送状态" class="kind" @change="reload">
          <el-option v-for="(label, k) in STATUS_LABELS" :key="k" :value="k" :label="label" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
        <el-button @click="exportXlsx">导出 Excel</el-button>
        <span class="dim">共 {{ total }} 条</span>
      </div>

      <DataCards v-if="narrow" :rows="items" title-key="title" v-loading="loading"
                 :fields="[{ key: 'rec', label: '收件人' },
                           { key: 't', label: '时间', format: fmtTime },
                           { key: 'template_label', label: '类型' },
                           { key: 'activity_title', label: '活动' },
                           { key: 'read', label: '已读', format: yesNo },
                           { key: 'ack', label: '签收' },
                           { key: 'trig', label: '触发人' }]"
                 empty="暂无记录">
        <template #extra="{ row }">
          <el-tag size="small" effect="plain" :type="STATUS_TYPES[row.status]">
            {{ row.status_label }}
          </el-tag>
        </template>
      </DataCards>

      <el-table v-else :data="items" size="small" v-loading="loading">
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="收件人" width="180">
          <template #default="{ row }">{{ row.rec }}</template>
        </el-table-column>
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.template_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="标题" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tooltip :content="row.content" placement="top" :show-after="300">
              <span>{{ row.title }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="activity_title" label="活动" min-width="160" show-overflow-tooltip />
        <el-table-column label="推送状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="STATUS_TYPES[row.status]">{{ row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="已读" width="70">
          <template #default="{ row }">{{ yesNo(row.read) }}</template>
        </el-table-column>
        <el-table-column label="签收" width="150">
          <template #default="{ row }">{{ row.ack }}</template>
        </el-table-column>
        <el-table-column label="触发人" width="180">
          <template #default="{ row }">{{ row.trig }}</template>
        </el-table-column>
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
import { useNarrow } from '../composables/useNarrow'
import DataCards from '../components/DataCards.vue'
import DateTimeField from '../components/DateTimeField.vue'

const SIZE = 20
const STATUS_LABELS = { ok: '成功', failed: '失败', site: '仅站内' }
const STATUS_TYPES = { ok: 'success', failed: 'danger', site: 'info' }
const narrow = useNarrow()
const items = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const templates = ref({})
const today = ref({ total: 0, failed: 0 })
const maxDays = ref(31)
const filter = ref({ start: '', end: '', person: '', activity: '', template: '', status: '' })

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
const yesNo = v => (v ? '是' : '否')

function ackText(row) {
  if (!row.need_ack) return '不需签收'
  return row.acked_at ? `已签收 ${fmtTime(row.acked_at)}` : '未签收'
}

function query() {
  const f = filter.value
  const q = new URLSearchParams()
  if (f.start) q.set('start', localText(f.start))
  if (f.end) q.set('end', localText(f.end))
  for (const k of ['person', 'activity', 'template', 'status']) if (f[k].trim()) q.set(k, f[k].trim())
  return q
}

async function load() {
  loading.value = true
  try {
    const q = query()
    q.set('page', page.value)
    q.set('size', SIZE)
    const d = await api.get(`/api/staff/notifications?${q}`)
    items.value = d.items.map(r => ({
      ...r,
      rec: r.recipient_name
        ? `${r.recipient_name}（${r.recipient_sid}）` : (r.recipient_sid || '未知'),
      trig: r.trigger_sid
        ? `${r.trigger_name}（${r.trigger_sid}）` : r.trigger_name,
      ack: ackText(r),
    }))
    total.value = d.total
    templates.value = d.templates
    today.value = d.today
    maxDays.value = d.max_days
  } finally { loading.value = false }
}

function reload() {
  page.value = 1
  load()
}

function exportXlsx() {
  download(`/api/staff/notifications.xlsx?${query()}`)
}

onMounted(load)
</script>

<style scoped>
.today { margin: 0 0 12px; }
.when { width: 200px; }
.field { width: 160px; }
.kind { width: 120px; }
.bad { color: var(--el-color-danger); }
@media (max-width: 860px) {
  .when, .field, .kind { width: 100%; }
}
</style>
