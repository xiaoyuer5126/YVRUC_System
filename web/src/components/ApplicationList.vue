<!-- 报名后筛选：申请名单。看答案与档案，勾人批量录取 / 不录取。 -->
<template>
  <el-card shadow="never" v-loading="loading">
    <template #header>
      <div class="al-head">
        <b>申请名单</b>
        <span class="dim">{{ pendingCount }} 人待筛选 · 共 {{ apps.length }} 人</span>
        <el-button size="small" link type="primary" @click="load">刷新</el-button>
        <el-button size="small" link type="primary" @click="exportXlsx">导出 Excel</el-button>
        <!-- 默认停在「只看待处理」：进这个页面多半就是把这一批处理掉 -->
        <el-radio-group v-model="pendingOnly" size="small" @change="onFilterChange">
          <el-radio-button :value="true">只看待处理</el-radio-button>
          <el-radio-button :value="false">全部</el-radio-button>
        </el-radio-group>
        <span class="dim">导出仍是全部申请</span>
        <span class="al-actions">
          <el-select v-model="targetShifts" multiple clearable collapse-tags
                     collapse-tags-tooltip size="small" style="width:260px"
                     placeholder="录取到：各自的第一意向">
            <el-option v-for="g in shifts" :key="g.id" :value="g.id"
                       :label="`${g.name}（${g.enrolled}/${g.capacity}）`" />
          </el-select>
          <!-- 选岗位的场次可以在这里统一改岗位；不选就用各人勾的意向岗位 -->
          <template v-for="g in targetPickShifts" :key="g.id">
            <el-select v-model="targetPositions[g.id]" clearable size="small"
                       style="width:200px" :placeholder="`${g.name}：按各人意向`">
              <el-option v-for="p in g.positions" :key="p.id" :value="p.id"
                         :label="`${g.name} · ${p.name}（${p.quota} 人）`" />
            </el-select>
          </template>
          <el-button type="success" size="small" :disabled="!selected.length"
                     :loading="busy" @click="decide(true)">录取</el-button>
          <el-button type="danger" size="small" plain :disabled="!selected.length"
                     :loading="busy" @click="decide(false)">不录取</el-button>
        </span>
      </div>
    </template>
    <p class="dim" style="margin:0 0 8px">
      一个人可以录到他意向里的一场或几场（时间不能重叠），没选中的意向场次随即作废。
      统一分配的场次先落在「待分配」，再分配岗位；不录取只发送通知，不留记录。
      场次开始时仍未处理的申请会自动落选并通知志愿者。
    </p>
    <el-table ref="tableRef" :data="shown" size="small" row-key="id"
              :empty-text="pendingOnly ? '没有待处理的申请' : '还没有人申请'"
              @selection-change="onSelect">
      <el-table-column type="selection" width="36" :selectable="selectable" />
      <el-table-column type="expand" width="30">
        <template #default="{ row }">
          <AnswerList :answers="row.answers" style="margin:4px 0 4px 30px" />
        </template>
      </el-table-column>
      <!-- 先来后到：序号是第几个交的申请，提交时间紧跟在姓名后面，不用拖到最右边看 -->
      <el-table-column label="序号" prop="seq" width="56" />
      <!-- 状态紧挨着序号：列多了一屏放不下，处理没处理要能一眼看见 -->
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="STATUS_TYPE[row.status]">{{ row.status_label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="姓名" width="110">
        <template #default="{ row }">
          <ScoreAlert :user="row.user" />
          <el-link type="primary" underline="never" @click="$emit('archive', row.user)">
            {{ row.user.name }}
          </el-link>
          <!-- 学号跟在名字下面一行，省下的这一列宽度留给状态 -->
          <div class="dim">{{ row.user.student_id }}</div>
        </template>
      </el-table-column>
      <el-table-column label="提交时间" width="110">
        <template #default="{ row }">{{ fmtDT(row.created_at) }}</template>
      </el-table-column>
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
      <!-- 录取到哪：默认他的第一意向，逐人可改成一场或几场、每场定岗位；
           上面那排下拉只是批量默认值 -->
      <el-table-column label="录取到" width="280">
        <template #default="{ row }">
          <template v-if="row.status === 'applied'">
            <el-select :model-value="pickOf(row).map(p => p.shift_id)" multiple
                       size="small" style="width:100%" placeholder="场次"
                       @update:model-value="v => setShifts(row, v)">
              <el-option v-for="w in row.wanted.filter(x => x.status === 'applied')"
                         :key="w.shift_id" :value="w.shift_id"
                         :label="shiftName(w.shift_id)" />
            </el-select>
            <div v-for="p in pickOf(row).filter(x => positionsOf(x.shift_id).length)"
                 :key="p.shift_id" class="al-pick">
              <span class="dim">{{ shiftName(p.shift_id) }}</span>
              <el-select :model-value="p.position_id" size="small" clearable
                         style="width:160px" placeholder="岗位（按意向）"
                         @update:model-value="v => setPosition(row, p.shift_id, v)">
                <el-option v-for="o in positionsOf(p.shift_id)" :key="o.id" :value="o.id"
                           :label="`${o.name}（${o.quota} 人）`" />
              </el-select>
            </div>
          </template>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!apps.length && !loading" description="还没有人申请" />
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
// 整批录取到哪几场；选岗位的场次再各定一个整批岗位（场次 id → 岗位 id）
const targetShifts = ref([])
const targetPositions = ref({})
const targetPickShifts = computed(() => shifts.value
  .filter(g => targetShifts.value.includes(g.id) && g.positions.length))
// 逐人指定的录取去向（[{shift_id, position_id}]）：只存改过的人，其余跟批量下拉走
const picks = ref({})   // vuecheck: script-only，模板只通过 pickOf 读它
const shiftName = id => shifts.value.find(g => g.id === id)?.name || id
const positionsOf = id => shifts.value.find(g => g.id === id)?.positions || []
const batchTargets = () => targetShifts.value
  .map(id => ({ shift_id: id, position_id: targetPositions.value[id] || '' }))
function pickOf(row) {
  const hit = picks.value[row.id]
  if (hit) return hit
  const wanted = row.wanted.find(w => w.status === 'applied') || row.wanted[0]
  const chosen = targetShifts.value.length ? batchTargets()
    : (wanted ? [{ shift_id: wanted.shift_id, position_id: '' }] : [])
  return chosen.map(p => ({ ...p, position_id: p.position_id || wantedPositionId(row, p.shift_id) }))
}
// 他在这一场勾的意向岗位；名单只给岗位名，按名字对回 id
function wantedPositionId(row, shiftId) {
  const name = row.wanted.find(w => w.shift_id === shiftId)?.position_name
  return (name && positionsOf(shiftId).find(p => p.name === name)?.id) || ''
}
function setShifts(row, ids) {
  const before = Object.fromEntries(pickOf(row).map(p => [p.shift_id, p.position_id]))
  picks.value[row.id] = ids.map(id => ({ shift_id: id, position_id: before[id] || '' }))
}
function setPosition(row, shiftId, v) {
  picks.value[row.id] = pickOf(row)
    .map(p => (p.shift_id === shiftId ? { ...p, position_id: v || '' } : p))
}
const loading = ref(false)
const busy = ref(false)
const pendingCount = computed(() => apps.value.filter(a => a.status === 'applied').length)
const pendingOnly = ref(true)
const shown = computed(() => (pendingOnly.value
  ? apps.value.filter(a => a.status === 'applied') : apps.value))
const tableRef = ref()
const selectable = row => row.status === 'applied'
const onSelect = rows => { selected.value = rows }
// 切开关要清勾选：行被筛掉以后，留着勾选会把看不见的人也一起提交
function onFilterChange() {
  tableRef.value?.clearSelection()
  selected.value = []
}

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
  const empty = selected.value.filter(a => !pickOf(a).length)
  if (accept && empty.length) {
    ElMessage.warning(`${empty.map(a => a.user.name).join('、')} 还没选录取到哪一场`)
    return
  }
  const names = selected.value.map(a => a.user.name).join('、')
  const target = targetShifts.value.map(shiftName).join('」「')
  await ElMessageBox.confirm(
    accept ? `录取 ${names} 到「${target || '各自的第一意向场次'}」？逐人改过的按各人所选。通知将立即发送。`
           : `不录取 ${names}？将发送通知，不留记录。`,
    accept ? '确认录取' : '确认不录取', { type: accept ? 'success' : 'warning' })
  busy.value = true
  try {
    const d = await api.post(`/api/staff/activities/${props.activityId}/applications/decide`,
      { application_ids: selected.value.map(a => a.id), accept,
        targets: batchTargets(),
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
.al-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.al-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.al-pick { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
</style>
