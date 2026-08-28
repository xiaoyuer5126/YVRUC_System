<template>
  <div v-loading="loading">
    <div class="toolbar">
      <el-button type="primary" size="small" @click="ensureTasks">
        <el-icon><Plus /></el-icon>&nbsp;生成领取 / 归还码
      </el-button>
      <el-button v-if="data?.tasks?.take" size="small" @click="openQR('take')">领取码</el-button>
      <el-button v-if="data?.tasks?.return" size="small" @click="openQR('return')">归还码</el-button>
      <el-button size="small" type="warning" plain :disabled="!data?.outstanding"
                 @click="remind">
        提醒未归还（{{ data?.outstanding || 0 }} 人）
      </el-button>
      <span class="dim">领取与归还各一个码，贴在两张桌子上；现场也可手动勾选</span>
    </div>

    <el-row :gutter="14" style="margin-bottom:12px">
      <el-col :span="6"><el-card shadow="never" class="num"><b>{{ data?.expected || 0 }}</b><div class="dim">应领</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never" class="num"><b>{{ (data?.taken || 0) + (data?.returned || 0) }}</b><div class="dim">已领</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never" class="num"><b>{{ data?.returned || 0 }}</b><div class="dim">已归还</div></el-card></el-col>
      <el-col :span="6"><el-card shadow="never" class="num warn"><b>{{ data?.outstanding || 0 }}</b><div class="dim">未归还</div></el-card></el-col>
    </el-row>

    <div class="toolbar" style="margin-bottom:8px">
      <template v-if="selected.length">
        <span class="dim">已选 {{ selected.length }} 人：</span>
        <el-button size="small" type="primary" @click="batch('take')">登记领取</el-button>
        <el-button size="small" type="warning" @click="batch('return')">登记归还</el-button>
        <el-button size="small" plain @click="batch('cancel_return')">撤销归还</el-button>
        <el-button size="small" type="danger" plain @click="batch('cancel_take')">撤销领取</el-button>
      </template>
      <span v-else class="dim">勾选后可批量登记领取／归还</span>
    </div>

    <DataCards v-if="narrow" :rows="data?.items || []" title-key="name"
               row-key="student_id"
               :fields="[{ key: 'student_id', label: '学号' },
                         { key: 'position', label: '岗位' },
                         { key: 'vest_no', label: '编号', hideEmpty: true }]"
               empty="还没有需要马甲的志愿者">
      <template #extra="{ row }">
        <el-tag size="small" :type="tagType(row)">{{ tagText(row) }}</el-tag>
      </template>
      <template #actions="{ row }">
        <el-button v-if="!row.taken && !row.returned" size="small" type="primary" plain
                   @click="one('take', row)">登记领取</el-button>
        <el-button v-else-if="row.taken" size="small" type="warning" plain
                   @click="one('return', row)">登记归还</el-button>
      </template>
    </DataCards>
    <el-table v-else :data="data?.items || []" size="small" max-height="360"
              @selection-change="onSelect">
      <el-table-column type="selection" width="40" />
      <el-table-column prop="name" label="姓名" width="110" />
      <el-table-column prop="student_id" label="学号" width="120" />
      <el-table-column prop="position" label="岗位" min-width="130" />
      <el-table-column label="编号" width="100">
        <template #default="{ row }">{{ row.vest_no || '—' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag size="small" :type="tagType(row)">{{ tagText(row) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="领取 / 归还" min-width="180">
        <template #default="{ row }">
          <span class="dim">{{ fmtDT(row.taken_at) || '—' }}</span>
          <span class="dim"> / {{ fmtDT(row.returned_at) || '—' }}</span>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="qr.visible" :title="'马甲' + qr.label + '码'" width="380">
      <div style="text-align:center" v-if="qr.token">
        <img :src="`/api/vests/qr.svg?token=${qr.token}&t=${Date.now()}`"
             width="260" height="260" alt="马甲二维码" />
        <div class="mono" style="font-size:22px;letter-spacing:4px">{{ qr.token }}</div>
        <div class="dim">请把这张码贴在{{ qr.label }}处，别和另一张弄混</div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from './DataCards.vue'

const props = defineProps({ activityId: { type: String, required: true } })
const narrow = useNarrow()
const data = ref(null)
const loading = ref(false)
const selected = ref([])
const qr = ref({ visible: false, token: '', label: '' })

async function load() {
  loading.value = true
  try {
    data.value = await api.get(`/api/staff/activities/${props.activityId}/vests`)
    selected.value = []
  } finally { loading.value = false }
}
onMounted(load)

const onSelect = rows => { selected.value = rows }
const tagText = r => (r.returned ? '已归还' : r.taken ? '未归还' : '未领取')
const tagType = r => (r.returned ? 'success' : r.taken ? 'warning' : 'info')

async function ensureTasks() {
  await api.post(`/api/staff/activities/${props.activityId}/vest-tasks`, {})
  ElMessage.success('领取码与归还码已就绪')
  load()
}

function openQR(action) {
  qr.value = { visible: true, token: data.value.tasks[action].token,
               label: action === 'take' ? '领取' : '归还' }
}

async function batch(action) {
  await api.post(`/api/staff/activities/${props.activityId}/vests/batch`,
    { action, student_ids: selected.value.map(r => r.student_id) })
  ElMessage.success('已登记')
  load()
}

async function one(action, row) {
  let vestNo = ''
  if (action === 'take') {
    try {
      const r = await ElMessageBox.prompt(`${row.name} 领取马甲`, '登记领取',
        { inputPlaceholder: '马甲编号（选填）', confirmButtonText: '登记',
          cancelButtonText: '取消', inputValue: row.vest_no || '' })
      vestNo = r.value || ''
    } catch { return }
  }
  await api.post(`/api/staff/activities/${props.activityId}/vests/batch`,
    { action, student_ids: [row.student_id], vest_no: vestNo })
  ElMessage.success('已登记')
  load()
}

async function remind() {
  const d = await api.post(`/api/staff/activities/${props.activityId}/vests/remind`, {})
  ElMessage.success(`已提醒 ${d.count} 位未归还的志愿者`)
}
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.num { text-align: center; border-radius: 12px; }
.num b { font-size: 26px; color: var(--ruc-red); }
.num.warn b { color: var(--el-color-warning); }
</style>
