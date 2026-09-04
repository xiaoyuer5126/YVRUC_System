<!-- 报名后筛选：申请名单。看答案与档案，勾人批量录取 / 不录取。 -->
<template>
  <el-card shadow="never" v-loading="loading">
    <template #header>
      <div class="al-head">
        <b>申请名单</b>
        <span class="dim">{{ pendingCount }} 人待筛选 · 共 {{ apps.length }} 人</span>
        <el-button size="small" link type="primary" @click="load">刷新</el-button>
        <el-button size="small" link type="primary" @click="exportXlsx">导出 Excel</el-button>
        <span class="al-actions">
          <el-select v-model="targetShift" clearable size="small" style="width:230px"
                     placeholder="录取到：各自的第一意向" @change="targetPosition = ''">
            <el-option v-for="g in shifts" :key="g.id" :value="g.id"
                       :label="`${g.name}（${g.enrolled}/${g.capacity}）`" />
          </el-select>
          <!-- 选岗位的场次可以在这里统一改岗位；不选就用各人勾的意向岗位 -->
          <el-select v-if="targetPositions.length" v-model="targetPosition" clearable
                     size="small" style="width:200px" placeholder="岗位：按各人意向">
            <el-option v-for="p in targetPositions" :key="p.id" :value="p.id"
                       :label="`${p.name}（${p.quota} 人）`" />
          </el-select>
          <el-button type="success" size="small" :disabled="!selected.length"
                     :loading="busy" @click="decide(true)">录取</el-button>
          <el-button type="danger" size="small" plain :disabled="!selected.length"
                     :loading="busy" @click="decide(false)">不录取</el-button>
        </span>
      </div>
    </template>
    <p class="dim" style="margin:0 0 8px">
      录取的人先落在所选场次的「待分配」里，再用一键分配或手动指派进具体岗位；
      不录取只发一条通知，不留任何记录。
    </p>
    <el-table :data="apps" size="small" row-key="id" @selection-change="onSelect">
      <el-table-column type="selection" width="36" :selectable="selectable" />
      <el-table-column type="expand" width="30">
        <template #default="{ row }">
          <el-descriptions :column="1" size="small" border style="margin:4px 0 4px 30px">
            <el-descriptions-item v-for="a in row.answers" :key="a.question_id" :label="a.title">
              <template v-if="a.kind === 'file'">
                <el-link v-if="a.value" type="primary" :underline="false"
                         @click="dl(`/api/materials/${a.value.attachment_id}`)">
                  {{ a.value.title }}
                </el-link>
                <span v-else class="dim">未上传</span>
              </template>
              <span v-else-if="Array.isArray(a.value)">{{ a.value.join('、') || '—' }}</span>
              <span v-else style="white-space:pre-wrap">{{ a.value || '—' }}</span>
            </el-descriptions-item>
          </el-descriptions>
        </template>
      </el-table-column>
      <el-table-column label="姓名" width="110">
        <template #default="{ row }">
          <ScoreAlert :user="row.user" />
          <el-link type="primary" :underline="false" @click="$emit('archive', row.user)">
            {{ row.user.name }}
          </el-link>
        </template>
      </el-table-column>
      <el-table-column label="学号" prop="user.student_id" width="110" />
      <el-table-column label="学院" prop="user.college" min-width="120" />
      <el-table-column label="组长意愿" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.leader_willing" size="small" type="warning" effect="plain">愿意</el-tag>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
      <el-table-column label="意向场次" min-width="180">
        <template #default="{ row }">
          <el-tag v-for="w in row.wanted" :key="w.shift_id" size="small" effect="plain"
                  style="margin:0 6px 2px 0"
                  :type="w.status === 'enrolled' ? 'success' : (w.status === 'applied' ? 'primary' : 'info')">
            {{ w.shift_name }}{{ w.position_name ? ' · ' + w.position_name : '' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="STATUS_TYPE[row.status]">{{ row.status_label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="提交时间" width="150">
        <template #default="{ row }">{{ fmtDT(row.created_at) }}</template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!apps.length && !loading" description="还没有人申请" :image-size="40" />
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, download as dl, fmtDT } from '../api'
import ScoreAlert from './ScoreAlert.vue'

const props = defineProps({ activityId: { type: String, required: true } })
const emit = defineEmits(['archive', 'changed'])

const STATUS_TYPE = { applied: 'primary', accepted: 'success', rejected: 'info', withdrawn: 'info' }
const apps = ref([])
const shifts = ref([])
const selected = ref([])
const targetShift = ref('')
const targetPosition = ref('')
const targetPositions = computed(
  () => shifts.value.find(g => g.id === targetShift.value)?.positions || [])
const loading = ref(false)
const busy = ref(false)
const pendingCount = computed(() => apps.value.filter(a => a.status === 'applied').length)
const selectable = row => row.status === 'applied'
const onSelect = rows => { selected.value = rows }

function exportXlsx() {
  dl(`/api/staff/activities/${props.activityId}/applications.xlsx`)
}

async function load() {
  loading.value = true
  try {
    const d = await api.get(`/api/staff/activities/${props.activityId}/applications`)
    apps.value = d.applications
    shifts.value = d.shifts
  } finally { loading.value = false }
}
onMounted(load)

async function decide(accept) {
  const names = selected.value.map(a => a.user.name).join('、')
  const target = shifts.value.find(g => g.id === targetShift.value)
  await ElMessageBox.confirm(
    accept ? `录取 ${names} 到「${target ? target.name : '各自的第一意向场次'}」？录取通知会立即发出。`
           : `不录取 ${names}？会发一条措辞留余地的通知，不留任何记录。`,
    accept ? '确认录取' : '确认不录取', { type: accept ? 'success' : 'warning' })
  busy.value = true
  try {
    const d = await api.post(`/api/staff/activities/${props.activityId}/applications/decide`,
      { application_ids: selected.value.map(a => a.id), accept,
        shift_id: targetShift.value || '', position_id: targetPosition.value || '' })
    ElMessage.success(`${accept ? '已录取' : '已处理'} ${d.count} 人`)
    await load()
    emit('changed')
  } finally { busy.value = false }
}
</script>

<style scoped>
.al-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.al-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
</style>
