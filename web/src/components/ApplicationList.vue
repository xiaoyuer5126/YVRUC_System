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
      录取的人先落在所选场次的「待分配」，再分配岗位；不录取只发送通知，不留记录。
      场次开始时仍未处理的申请会自动落选并通知志愿者。
    </p>
    <el-table :data="apps" size="small" row-key="id" @selection-change="onSelect">
      <el-table-column type="selection" width="36" :selectable="selectable" />
      <el-table-column type="expand" width="30">
        <template #default="{ row }">
          <AnswerList :answers="row.answers" style="margin:4px 0 4px 30px" />
        </template>
      </el-table-column>
      <el-table-column label="姓名" width="110">
        <template #default="{ row }">
          <ScoreAlert :user="row.user" />
          <el-link type="primary" underline="never" @click="$emit('archive', row.user)">
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
                  style="margin:0 8px 4px 0"
                  :type="w.status === 'enrolled' ? 'success' : (w.status === 'applied' ? 'primary' : 'info')">
            {{ w.shift_name }}{{ w.position_name ? ' · ' + w.position_name : '' }}
          </el-tag>
        </template>
      </el-table-column>
      <!-- 录取到哪：默认他的第一意向，逐人可改；上面那对下拉只是批量默认值 -->
      <el-table-column label="录取到" width="330">
        <template #default="{ row }">
          <template v-if="row.status === 'applied'">
            <el-select :model-value="pickOf(row).shift_id" size="small"
                       style="width:150px" placeholder="场次"
                       @update:model-value="v => setShift(row, v)">
              <el-option v-for="w in row.wanted.filter(x => x.status === 'applied')"
                         :key="w.shift_id" :value="w.shift_id"
                         :label="shiftName(w.shift_id)" />
            </el-select>
            <el-select :model-value="pickOf(row).position_id" size="small" clearable
                       style="width:160px;margin-left:8px" placeholder="岗位（按意向）"
                       @update:model-value="v => setPosition(row, v)">
              <el-option v-for="p in positionsFor(row)" :key="p.id" :value="p.id"
                         :label="`${p.name}（${p.quota} 人）`" />
            </el-select>
          </template>
          <span v-else class="dim">—</span>
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
import { api, downloadExport, fmtDT } from '../api'
import ScoreAlert from './ScoreAlert.vue'
import AnswerList from './AnswerList.vue'

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
// 逐人指定的录取去向：只存改过的人，其余跟批量下拉走
const picks = ref({})   // vuecheck: script-only，模板只通过 pickOf 读它
const shiftName = id => shifts.value.find(g => g.id === id)?.name || id
function pickOf(row) {
  const hit = picks.value[row.id]
  if (hit) return hit
  const wanted = row.wanted.find(w => w.status === 'applied') || row.wanted[0] || {}
  const shift_id = targetShift.value || wanted.shift_id || ''
  const wantedPos = row.wanted.find(w => w.shift_id === shift_id)
  return { shift_id,
           position_id: targetPosition.value
             || positionIdByName(shift_id, wantedPos?.position_name) }
}
const positionIdByName = (shiftId, name) => (name
  ? shifts.value.find(g => g.id === shiftId)?.positions
      .find(p => p.name === name)?.id || '' : '')
const positionsFor = row =>
  shifts.value.find(g => g.id === pickOf(row).shift_id)?.positions || []
function setShift(row, v) {
  picks.value[row.id] = { shift_id: v, position_id: '' }
}
function setPosition(row, v) {
  picks.value[row.id] = { ...pickOf(row), position_id: v || '' }
}
const loading = ref(false)
const busy = ref(false)
const pendingCount = computed(() => apps.value.filter(a => a.status === 'applied').length)
const selectable = row => row.status === 'applied'
const onSelect = rows => { selected.value = rows }

// 只下 Excel；有上传的文件时再问要不要一起下附件包
function exportXlsx() {
  downloadExport(`/api/staff/activities/${props.activityId}/applications`)
}

async function load() {
  loading.value = true
  try {
    const d = await api.get(`/api/staff/activities/${props.activityId}/applications`)
    apps.value = d.applications
    shifts.value = d.shifts
    picks.value = {}
  } finally { loading.value = false }
}
onMounted(load)

async function decide(accept) {
  const names = selected.value.map(a => a.user.name).join('、')
  const target = shifts.value.find(g => g.id === targetShift.value)
  await ElMessageBox.confirm(
    accept ? `录取 ${names} 到「${target ? target.name : '各自的第一意向场次'}」？通知将立即发送。`
           : `不录取 ${names}？将发送通知，不留记录。`,
    accept ? '确认录取' : '确认不录取', { type: accept ? 'success' : 'warning' })
  busy.value = true
  try {
    const d = await api.post(`/api/staff/activities/${props.activityId}/applications/decide`,
      { application_ids: selected.value.map(a => a.id), accept,
        shift_id: targetShift.value || '', position_id: targetPosition.value || '',
        picks: Object.fromEntries(selected.value
          .filter(a => picks.value[a.id])
          .map(a => [a.id, picks.value[a.id]])) })
    ElMessage.success(`${accept ? '已录取' : '已处理'} ${d.count} 人`)
    await load()
    emit('changed')
  } finally { busy.value = false }
}
</script>

<style scoped>
.al-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.al-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
</style>
