<!-- 一键分配与导入排班：只给「截止后统一分配」的场次用，只排「待分配」里的人。
     两条路出来的是同一种方案，在这里过目、可改，确认后走同一个提交接口。 -->
<template>
  <div v-loading="loading || solving">
    <input ref="fileInput" type="file" accept=".xlsx" hidden @change="onImportFile" />

    <!-- 一键分配、导入、补招这些动作放在父级「已报名名单」那一行；这里只留试算选项 -->
    <div class="toolbar">
      <el-checkbox v-model="continuity" size="small">尽量同一人同一岗位</el-checkbox>
      <el-tooltip placement="top"
                  content="本活动其它场次排过同名岗位的人，优先留在该岗位">
        <el-icon class="dim"><QuestionFilled /></el-icon>
      </el-tooltip>
      <span style="margin-left:auto" class="dim" v-if="preview">
        已报名 {{ preview.total_enrolled }} 人 · 建议 {{ preview.total_assigned }} 人上岗
      </span>
    </div>

    <div class="dim" style="margin-bottom:12px">
      一键分配只排「待分配」里的人，已经在岗位上的人不动：按报名先后匹配尚有空位且
      满足条件的岗位，不满足条件的不排入，可勾选「强制」。导入排班用「导出排班表」的
      文件，改好「岗位」「组长」两列再传回来。
    </div>

    <template v-if="preview">
      <el-alert v-if="preview.errors?.length" type="warning" :closable="false"
                :title="`排班表里有 ${preview.errors.length} 行没有导入`">
        <div v-for="e in preview.errors" :key="e.row">第 {{ e.row }} 行：{{ e.message }}</div>
      </el-alert>
      <el-alert v-if="importLeaders.length" type="info" :closable="false"
                :title="`确认发布后，把 ${importLeaders.length} 人设为组长（排班表「组长」列）`" />
      <!-- 岗位统计 -->
      <div class="section-title">各岗位建议人数</div>
      <el-table :data="preview.position_stats" size="small" border style="margin-bottom:12px">
        <el-table-column prop="name" label="岗位" min-width="120" />
        <el-table-column prop="location" label="地点" min-width="110" />
        <el-table-column prop="requirement_desc" label="条件" min-width="120" />
        <el-table-column label="组长" width="70">
          <template #default="{row}">
            <el-tag v-if="row.needs_leader" size="small" type="warning" effect="plain">需要</el-tag>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="名额" width="110">
          <template #default="{row}">{{ row.quota }}</template>
        </el-table-column>
        <el-table-column label="已在岗" width="80">
          <template #default="{row}">{{ row.existing }}</template>
        </el-table-column>
        <el-table-column label="建议排入" width="90">
          <template #default="{row}"><b>{{ row.planned }}</b></template>
        </el-table-column>
        <el-table-column label="缺口" width="80">
          <template #default="{row}">
            <span :style="{color: gap(row) > 0 ? '#e6a23c' : '#67c23a'}">{{ gap(row) }}</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="section-title">分配明细（可直接修改"建议岗位"列再应用）</div>
      <div class="scroll-x">
      <el-table :data="rows" size="small" border max-height="420" style="min-width:720px">
        <el-table-column label="姓名" width="110">
          <template #default="{row}">
            <b>{{ row.user.name }}</b>
            <el-tooltip v-if="row.leader_willing" content="愿意担任组长" placement="top">
              <el-tag size="small" type="warning" effect="plain"
                      style="margin-left:4px">愿任组长</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="user.student_id" label="学号" width="110" />
        <el-table-column prop="user.college" label="学院" width="120" />
        <el-table-column label="报名时间" width="150">
          <template #default="{row}"><span class="dim">{{ fmtDTShort(row.joined_at) }}</span></template>
        </el-table-column>
        <el-table-column label="原岗位" width="100">
          <template #default="{row}">{{ stationName(row.current_position_id) || '未分配' }}</template>
        </el-table-column>
        <el-table-column label="本人意愿" width="100">
          <template #default="{row}">
            <el-tooltip placement="top"
                        :content="row.allow_reassign ? '本人接受岗位调剂'
                                                     : '本人不接受调剂，调整将计入留痕'">
              <el-tag size="small" effect="plain"
                      :type="row.allow_reassign ? 'success' : 'info'">
                {{ row.allow_reassign ? '接受调剂' : '不接受' }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="建议岗位" min-width="200">
          <template #default="{row}">
            <el-select v-model="row.target" size="small" clearable placeholder="留在待分配">
              <el-option v-for="s in targetOpts" :key="s.value" :value="s.value"
                         :label="`${s.label}（余${s.left}）`" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="变化" width="90">
          <template #default="{row}">
            <el-tag v-if="!row.target" size="small" type="info" effect="plain">待分配</el-tag>
            <el-tag v-else-if="row.target === row.current_position_id" size="small" type="success" effect="plain">保持</el-tag>
            <el-tag v-else-if="row.continuity" size="small" type="success" effect="plain">同岗延续</el-tag>
            <el-tag v-else-if="isWaiting(row)" size="small" type="primary" effect="plain">新排入</el-tag>
            <el-tag v-else size="small" :type="row.allow_reassign ? 'warning' : 'danger'"
                    effect="plain">
              {{ row.allow_reassign ? '调整' : '调整(违意愿)' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="超额强制" width="90">
          <template #default="{row}">
            <el-tooltip content="名额已满仍要排入时勾选，计入留痕" placement="top">
              <el-checkbox v-model="row.force" :disabled="!overQuota(row)" />
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>
      </div>

      <div v-if="preview.unassigned.length" class="section-title">
        暂未能分配（{{ preview.unassigned.length }} 人）
      </div>
      <el-tag v-for="u in preview.unassigned" :key="u.user.id" type="danger" effect="plain"
              style="margin-right:8px;margin-bottom:4px">
        {{ u.user.name }}（{{ u.user.student_id }}）· {{ u.reason }}
      </el-tag>

      <el-alert v-if="unwillingMoved" type="warning" :closable="false"
                :title="`有 ${unwillingMoved} 人报名时选择了不接受调剂，本方案仍会调换其岗位`" />

      <div style="margin-top:12px;text-align:right">
        <div class="btn-row">
          <el-button @click="resetEdit">还原为系统建议</el-button>
          <el-button type="primary" :loading="applying" @click="apply">确认并发布分配</el-button>
        </div>
        <div class="dim" style="margin-top:8px">发布后将向每人发送岗位通知</div>
      </div>
    </template>

    <el-empty v-else description="点击名单上方的「一键分配岗位」开始试算，或「导入排班」" />

    <!-- 补招：负责人直接把人加进场次 -->
    <el-dialog v-model="walkinVisible" title="补招志愿者" width="430">
      <el-form label-width="90px">
        <el-form-item label="学号"><el-input v-model="walkin.sid" /></el-form-item>
        <el-form-item label="指定岗位">
          <el-select v-model="walkin.position" clearable style="width:100%">
            <el-option v-for="s in stationOpts" :key="s.value" :value="s.value" :label="s.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="调剂意愿">
          <el-checkbox v-model="walkin.allowReassign">本人接受岗位调剂</el-checkbox>
        </el-form-item>
        <!-- 补招不自动连带培训/彩排：赶不赶得上、要不要去，由负责人勾 -->
        <el-form-item v-if="walkinAttached.length" label="培训/彩排">
          <el-checkbox-group v-model="walkin.attached">
            <el-checkbox v-for="a in walkinAttached" :key="a.id" :value="a.id"
                         style="display:block">
              {{ a.kind_label }}「{{ a.name }}」<span class="dim">{{ a.time_text }}</span>
            </el-checkbox>
          </el-checkbox-group>
          <div class="dim">默认不报；勾上的一并报上</div>
        </el-form-item>
        <!-- 党校同学只能从这里加进来 -->
        <el-form-item label="党校同学">
          <el-checkbox v-model="walkin.party">是党校同学</el-checkbox>
          <div class="dim">时长带「党校」标签，不进时长导出表，也不计入本人的累计时长</div>
        </el-form-item>
        <el-form-item label="">
          <el-checkbox v-model="walkin.force">
            名额已满时强制加入，计入留痕（时段冲突会另外弹窗确认）
          </el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="walkinVisible = false">取消</el-button>
        <el-button type="primary" @click="addWalkin">确认补招</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, fmtDTShort, uploadFile, withConflictConfirm } from '../api'

const props = defineProps({
  shift: { type: Object, required: true },
})
const emit = defineEmits(['reload'])

const loading = ref(false)
const solving = ref(false)
const applying = ref(false)
const continuity = ref(true)
const preview = ref(null)
const rows = ref([])

// 父级的「已报名名单」那一行触发试算与补招。函数会提升，ref 不会——
// 暴露给父级的东西必须在声明之后再交出去
function openWalkin() { walkinVisible.value = true }
defineExpose({ runPreview, openImport, openWalkin, solving })
const walkinVisible = ref(false)
const walkin = ref({ sid: '', position: '', force: false, allowReassign: true,
                     attached: [], party: false })
// 本场次下对所选岗位生效的培训/彩排（没选岗位就全列），口径同后端 attached_shifts
const walkinAttached = computed(() => (props.shift.attached || []).filter(a =>
  !walkin.value.position || !a.applies_to_positions?.length
  || a.applies_to_positions.includes(walkin.value.position)))

const stationOpts = computed(() =>
  props.shift.positions.map(s => ({
    value: s.id, label: s.name,
    left: Math.max(0, s.quota - (s.enrolled || 0)),
  })))
const holderId = computed(  // vuecheck: script-only，模板经 isWaiting、targetOpts 读它
  () => props.shift.positions.find(s => s.is_placeholder)?.id)
// 方案里能选的去处：真实岗位。清空就是留在 / 放回「待分配」
const targetOpts = computed(() => stationOpts.value.filter(s => s.value !== holderId.value))
const isWaiting = row => !row.current_position_id || row.current_position_id === holderId.value

function stationName(sid) {
  const s = props.shift.positions.find(x => x.id === sid)
  return s ? s.name : ''
}

// 本人不接受调剂、方案又把他挪出原岗位的人数（只提示，不拦）
const unwillingMoved = computed(() => rows.value.filter(
  r => !r.allow_reassign && !isWaiting(r)
       && (r.target || holderId.value) !== r.current_position_id).length)

// 编辑态行
function buildRows() {
  rows.value = preview.value.plan.map(r => ({
    ...r, target: r.position_id || '', force: false, over: false,
  }))
}
function resetEdit() { buildRows() }
function overQuota(row) {
  const s = props.shift.positions.find(x => x.id === row.target)
  return s ? s.remaining <= 0 : false
}

const gap = row => row.quota - row.existing - row.planned

/** 点了之前先看要不要：报名即选岗位的场次人人自己选好了；截止后分配的场次
 *  没人在待分配，也就没什么可排的。后端同样拦，这里先给一句好懂的话。 */
function notNeeded(forImport) {
  if (!props.shift.needs_assign) {
    ElMessage.info('本场次是「报名即选岗位」，志愿者报名时已各自选好岗位，不需要排班；'
                   + '个别调整请用名单里的「调岗」')
    return true
  }
  if (!props.shift.positions.some(s => !s.is_placeholder)) {
    ElMessage.warning('请先在「岗位设置」里为本场次建好岗位')
    return true
  }
  if (!forImport && !props.shift.unassigned_count) {
    ElMessage.info('本场次没有待分配的同学，不需要一键分配；要整体调整可以导入排班')
    return true
  }
  return false
}

async function runPreview() {
  if (notNeeded(false)) return
  solving.value = true
  try {
    preview.value = await api.post(
      `/api/staff/shifts/${props.shift.id}/assign/preview`,
      { continuity: continuity.value })
    importLeaders.value = []
    buildRows()
  } finally { solving.value = false }
}

// —— 导入排班 ——
const fileInput = ref(null)
const importLeaders = ref([])      // 排班表「组长」列标了的人，发布后设为组长
function openImport() {
  if (notNeeded(true)) return
  fileInput.value.value = ''
  fileInput.value.click()
}
async function onImportFile(e) {
  const file = e.target.files?.[0]
  if (!file) return
  solving.value = true
  try {
    const d = await uploadFile(`/api/staff/shifts/${props.shift.id}/assign/import`, file)
    if (!d.plan.length && !d.errors.length) {
      ElMessage.info('排班表里的岗位和现在一样，没有要调整的人')
      return
    }
    preview.value = d
    importLeaders.value = d.leaders
    buildRows()
  } finally { solving.value = false }
}

async function apply() {
  const fullPlan = rows.value.map(r => ({
    enrollment_id: r.enrollment_id, position_id: r.target, force: r.force }))
  applying.value = true
  try {
    const d = await api.post(`/api/staff/shifts/${props.shift.id}/assign/apply`,
      { plan: fullPlan, source: 'assign' })
    // 组长要等人落到岗位上才能设，所以放在发布之后逐个设
    for (const id of importLeaders.value) {
      await api.post(`/api/staff/enrollments/${id}/leader`, { is_leader: true })
    }
    ElMessage.success(d.summary + (d.applied ? '，通知已发送' : '')
                      + (importLeaders.value.length ? `；已设 ${importLeaders.value.length} 位组长` : ''))
    preview.value = null
    importLeaders.value = []
    emit('reload')
  } catch (e) {
    ElMessage.error(e.message + '，可勾选「强制」后重试')
  } finally { applying.value = false }
}

async function addWalkin() {
  try {
    // 时段冲突先弹窗确认，确认了带 confirm_conflict 重发；名额超出照旧要勾强制
    const d = await withConflictConfirm(confirm => api.post(
      `/api/staff/shifts/${props.shift.id}/add-walkin`, {
        student_id: walkin.value.sid, position_id: walkin.value.position || '',
        allow_reassign: walkin.value.allowReassign,
        attached_ids: walkin.value.attached.filter(
          id => walkinAttached.value.some(a => a.id === id)),
        force: walkin.value.force, party_school: walkin.value.party,
        confirm_conflict: confirm }),
    { head: `${walkin.value.sid} 与以下场次时间冲突：`, ask: '仍要补招？' })
    if (!d) return
    ElMessage.success('补招成功')
    walkinVisible.value = false
    walkin.value = { sid: '', position: '', force: false, allowReassign: true,
                     attached: [], party: false }
    emit('reload')
  } catch (e) {
    // 超额需要显式勾选强制，替用户把勾打上并提示重试
    if ((e.message || '').includes('强制')) {
      walkin.value.force = true
    }
  }
}
</script>

<style scoped>
.scroll-hint { margin: -8px 0 8px; }
@media (min-width: 861px) { .scroll-hint { display: none; } }
</style>
