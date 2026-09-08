<template>
  <div v-loading="loading || solving">
    <!-- 模式说明 -->
    <el-alert v-if="pickModeAllAssigned" type="info" :closable="false" style="margin-bottom:10px"
              title="本场次报名时已各自选定岗位"
              description="试算会保持现有岗位，可直接修改下方的建议岗位。" />

    <!-- 一键分配、补招这些动作放在父级「已报名名单」那一行；这里只留试算选项 -->
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
      按报名先后匹配尚有名额且满足条件的岗位，不满足条件的不排入，可勾选「强制」。
    </div>


    <template v-if="preview">
      <!-- 岗位统计 -->
      <div class="section-title">各岗位建议人数</div>
      <el-table :data="preview.position_stats" size="small" border style="margin-bottom:14px">
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
          <template #default="{row}">
            {{ row.quota }}
            <el-tooltip v-if="row.held" content="临期退选扣留的名额，分配不占用">
              <span class="dim">（扣留 {{ row.held }}）</span>
            </el-tooltip>
          </template>
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
        <el-table-column label="报名时间" width="110">
          <template #default="{row}"><span class="dim">{{ (row.joined_at || '').slice(5,16).replace('T',' ') }}</span></template>
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
            <el-select v-model="row.target" size="small" clearable placeholder="未分配">
              <el-option v-for="s in stationOpts" :key="s.value" :value="s.value"
                         :label="`${s.label}（余${s.left}）`" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="变化" width="90">
          <template #default="{row}">
            <el-tag v-if="!row.target" size="small" type="danger" effect="plain">不排</el-tag>
            <el-tag v-else-if="row.target === row.current_position_id" size="small" type="success" effect="plain">保持</el-tag>
            <el-tag v-else-if="row.continuity" size="small" type="success" effect="plain">同岗延续</el-tag>
            <el-tag v-else-if="!row.current_position_id" size="small" type="primary" effect="plain">新排入</el-tag>
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
                style="margin-top:12px"
                :title="`有 ${unwillingMoved} 人报名时选择了不接受调剂，本方案仍会调换其岗位`" />

      <div style="margin-top:14px;text-align:right">
        <el-button @click="resetEdit">还原为系统建议</el-button>
        <el-button type="primary" :loading="applying" @click="apply">确认并发布分配</el-button>
        <div class="dim" style="margin-top:6px">发布后将向每人发送岗位通知</div>
      </div>
    </template>

    <el-empty v-else description="点击名单上方的「一键分配岗位」开始试算" :image-size="80" />

    <!-- 补招：现场临时加人 -->
    <el-dialog v-model="walkinVisible" title="补招志愿者（现场添加）" width="430">
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
        <el-form-item label="">
          <el-checkbox v-model="walkin.force">
            时段冲突或名额已满时强制加入，计入留痕
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
import { api } from '../api'

const props = defineProps({
  shift: { type: Object, required: true },
  unassignedCount: { type: Number, default: 0 },   // 已报名但还没有岗位的人数
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
defineExpose({ runPreview, openWalkin, solving })
const walkinVisible = ref(false)
const walkin = ref({ sid: '', position: '', force: false, allowReassign: true })

// 报名即选岗位且人人有岗 → 提示即可，不禁止试算
const pickModeAllAssigned = computed(() =>
  !props.shift.needs_assign && props.unassignedCount === 0)

const stationOpts = computed(() =>
  props.shift.positions.map(s => ({
    value: s.id, label: s.name,
    left: Math.max(0, s.quota - (s.enrolled || 0)),
  })))

function stationName(sid) {
  const s = props.shift.positions.find(x => x.id === sid)
  return s ? s.name : ''
}

// 本人不接受调剂、方案又把他挪出原岗位的人数（只提示，不拦）
const unwillingMoved = computed(() => rows.value.filter(
  r => !r.allow_reassign && r.current_position_id && r.target
       && r.target !== r.current_position_id).length)

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

// 缺口按「可用名额」算：扣留的位子填不进人，不该算成缺口
const gap = row => row.quota - row.held - row.planned

async function runPreview() {
  if (!props.shift.positions.length) {
    ElMessage.warning('请先在「岗位设置」中创建岗位')
    return
  }
  solving.value = true
  try {
    preview.value = await api.post(
      `/api/staff/shifts/${props.shift.id}/assign/preview`,
      { continuity: continuity.value })
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
    ElMessage.success(d.summary + (d.applied ? '，通知已发送' : ''))
    preview.value = null
    emit('reload')
  } catch (e) {
    ElMessage.error(e.message + '，可勾选「强制」后重试')
  } finally { applying.value = false }
}

async function addWalkin() {
  try {
    await api.post(`/api/staff/shifts/${props.shift.id}/add-walkin`, {
      student_id: walkin.value.sid, position_id: walkin.value.position || '',
      allow_reassign: walkin.value.allowReassign,
      force: walkin.value.force })
    ElMessage.success('补招成功')
    walkinVisible.value = false
    walkin.value = { sid: '', position: '', force: false, allowReassign: true }
    emit('reload')
  } catch (e) {
    // 冲突/超额都需要显式勾选强制，替用户把勾打上并提示重试
    if ((e.message || '').includes('强制')) {
      walkin.value.force = true
    }
  }
}
</script>

<style scoped>
.scroll-hint { margin: -6px 0 6px; }
@media (min-width: 861px) { .scroll-hint { display: none; } }
.toolbar { display:flex; gap:10px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }
</style>
