<!-- 活动管理「审核」页签：这场活动要过的三道审核放在一处看。
     立项审核（发布前）→ 时长审核（结项，一并填志愿北京活动名称）→ 志愿北京外联确认。
     时长锁定后名称推给志愿北京对接部门，外联在志愿北京核对确认，活动才算已完成；
     退回的话在这里改名称重提（时长保持锁定）。
     提交动作各在原处（提交审批在页头、提交时长审核在「时长管理」），这里看进度、
     意见与历史，并管理各自要附的材料。 -->
<template>
  <div v-loading="loading">
    <!-- ① 立项审核 -->
    <el-card shadow="never">
      <template #header>
        <b>立项审核</b>
        <el-tag v-if="approval" size="small" effect="plain" style="margin-left:8px">
          {{ approval.status_label }}
        </el-tag>
        <span v-if="approval?.department" class="dim" style="margin-left:8px">
          归口：{{ approval.department }}
          <template v-if="act?.status === 'pending'">
            · 等待 {{ approval.on_duty ? approval.on_duty + '（值班）' : approval.assignee }} 处理
          </template>
        </span>
      </template>
      <el-timeline v-if="approval?.events?.length" style="padding-left:2px">
        <el-timeline-item v-for="(e, i) in approval.events" :key="i"
                          :timestamp="fmtDT(e.created_at)" :type="EVENT_TYPE[e.kind]">
          {{ e.actor }} {{ e.kind_label }}<template v-if="e.to"> → {{ e.to }}</template>
          <div v-if="e.comment" class="dim pre">{{ e.comment }}</div>
        </el-timeline-item>
      </el-timeline>
      <div v-else class="dim" style="margin-bottom:8px">还没有提交过审批（在页头点「提交审批」）。</div>
      <!-- 立项书只在对外活动里出现：审批中可换，通过后留着下载打印。
           上传入口在「提交审批」弹窗里；草稿阶段就摆出来，内部活动的发起方
           会以为自己也得传一份 -->
      <template v-if="files?.external && act && act.status !== 'draft'">
        <ProjectFileSlot :activity-id="activityId" kind="proposal" label="立项书"
                         :file="files.proposal" :can-upload="files.can_upload_proposal"
                         :block="files.manager ? files.proposal_block : ''"
                         :exts="files.proposal_exts" :template="templates.proposal"
                         :required="files.external && !files.proposal" @changed="load" />
      </template>
    </el-card>


    <!-- ② 时长审核 -->
    <el-card shadow="never" style="margin-top:12px">
      <template #header>
        <b>时长审核</b>
        <span class="dim" style="margin-left:8px">
          在「时长管理」核准后提交；
          <template v-if="hours?.handler">由 {{ hours.handler }} 审核</template>
        </span>
      </template>
      <template v-if="files?.external">
        <div class="dim">
          对外活动提交时长审核必须附盖章立项书（即盖章项目申请书）和盖章结项书（即盖章时长认证明细）；活动发布后、时长锁定前可随时更换。
          每份只留一个文件，多页的请合成一个 PDF。
        </div>
        <ProjectFileSlot v-for="s in STAMPED" :key="s.kind" :activity-id="activityId"
                         :kind="s.kind" :label="s.label" :file="files[s.kind]"
                         :can-upload="files[`can_upload_${s.kind}`]"
                         :block="files.manager ? files[`${s.kind}_block`] : ''"
                         :exts="files[`${s.kind}_exts`]" :template="templates[s.template]"
                         :required="!files[s.kind]" @changed="load" />
        <el-divider style="margin:8px 0" />
      </template>
      <el-table :data="hours?.items || []" size="small" empty-text="还没有提交过时长审核">
        <el-table-column label="类型" width="70" prop="kind_label" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="REQ_TAG[row.status]">
              {{ row.status_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="提交" min-width="200">
          <template #default="{ row }">
            {{ row.requested_by }} · {{ fmtDT(row.created_at) }}
            <div class="dim">{{ row.reason }}</div>
          </template>
        </el-table-column>
        <el-table-column label="审核" min-width="180">
          <template #default="{ row }">
            <template v-if="row.handled_by">
              {{ row.handled_by }} · {{ fmtDT(row.handled_at) }}
              <div v-if="row.reply" class="dim pre">意见：{{ row.reply }}</div>
            </template>
            <span v-else class="dim">等待审核</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ③ 志愿北京外联确认 -->
    <el-card shadow="never" style="margin-top:12px">
      <template #header>
        <b>志愿北京外联确认</b>
        <el-tag v-if="act?.status === 'vb_pending'" size="small" type="warning" effect="plain"
                style="margin-left:8px">待外联确认</el-tag>
        <span class="dim" style="margin-left:8px">
          时长锁定后，志愿北京活动名称推给{{ vbDept }}
          <template v-if="vbc.handler">（{{ vbc.handler }}）</template>核对，确认即完成
        </span>
      </template>
      <!-- 提交时长审核之前：先把名称存下来，申诉期满自动提交要用 -->
      <template v-if="vbc.can_edit">
        <VbNamesInput v-model="draftNames" />
        <div class="toolbar" style="margin-top:8px">
          <el-button type="primary" :loading="vbBusy" @click="saveNames">保存名称</el-button>
          <span class="dim">提交时长审核时须至少填一个；申诉期满系统自动提交时也用这里存的名称</span>
        </div>
      </template>
      <template v-else-if="vbc.can_resubmit">
        <el-alert type="error" show-icon :closable="false" style="margin-bottom:8px"
                  :title="`${vbDept}退回：${vbc.items[0]?.reply || ''}`"
                  description="改好志愿北京活动名称后重新提交给外联；时长保持锁定，不用重新审核。" />
        <VbNamesInput v-model="draftNames" />
        <div class="toolbar" style="margin-top:8px">
          <el-button type="primary" :loading="vbBusy" @click="resubmit">重新提交给外联</el-button>
        </div>
      </template>
      <template v-else-if="vbc.can_repush">
        <el-alert type="info" show-icon :closable="false" style="margin-bottom:8px"
                  title="活动已完成"
                  description="志愿北京上还没录到、或者建错了重建一个时，改好名称再推一次给外联。确认或退回都只更新记录，不影响活动状态。" />
        <VbNamesInput v-model="draftNames" />
        <el-input v-model="repushReason" type="textarea" :rows="2" maxlength="500"
                  show-word-limit style="margin-top:8px"
                  placeholder="推送原因（必填，外联会看到），如：志愿北京上漏录了这场，请补录" />
        <div class="toolbar" style="margin-top:8px">
          <el-button type="primary" :loading="vbBusy" @click="repush">再次推送给外联</el-button>
        </div>
      </template>
      <div v-else class="submit-names">
        <b>志愿北京活动名称</b>
        <template v-if="vbc.names.length">
          <el-tag v-for="n in vbc.names" :key="n" effect="plain">{{ n }}</el-tag>
        </template>
        <span v-else class="dim">未填写</span>
        <div v-if="vbc.edit_block" class="dim" style="flex-basis:100%">{{ vbc.edit_block }}</div>
      </div>
      <el-table :data="vbc.items" size="small" empty-text="时长锁定后才推给外联"
                style="margin-top:8px">
        <el-table-column label="推送" width="160">
          <template #default="{ row }">
            {{ row.kind_label }}
            <div class="dim">{{ row.submitted_by }} · {{ fmtDT(row.submitted_at) }}</div>
            <div v-if="row.reason" class="dim pre">原因：{{ row.reason }}</div>
          </template>
        </el-table-column>
        <el-table-column label="志愿北京活动名称" min-width="200">
          <template #default="{ row }">
            <div v-for="n in row.names" :key="n">{{ n }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="VBC_TAG[row.status]">
              {{ row.status_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="外联" min-width="180">
          <template #default="{ row }">
            <template v-if="row.handled_by">
              {{ row.handled_by }} · {{ fmtDT(row.handled_at) }}
              <div v-if="row.reply" class="dim pre">{{ row.status === 'returned' ? '理由' : '意见' }}：{{ row.reply }}</div>
            </template>
            <template v-else-if="row.status === 'pending' && vbc.can_decide">
              <div class="btn-row">
                <el-button size="small" link type="success" @click="openDecide(row, true)">确认</el-button>
                <el-button size="small" link type="danger" @click="openDecide(row, false)">退回</el-button>
              </div>
            </template>
            <span v-else class="dim">{{ row.status === 'pending' ? '等待确认' : '—' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <ApprovalCommentDialog v-model="dc.visible" :approve="dc.approve"
                           :title="dc.approve ? '确认志愿北京' : '退回志愿北京名称'"
                           :subject="act?.title || ''" audience="活动管理者"
                           approve-label="确认" reject-label="退回"
                           :templates="[vbConfirmed(dc.row?.names || [])]"
                           :submit="submitDecide" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, fmtDT } from '../api'
import { auth } from '../store'
import ApprovalCommentDialog from './ApprovalCommentDialog.vue'
import { vbConfirmed } from '../replyTemplates'
import ProjectFileSlot from './ProjectFileSlot.vue'
import VbNamesInput from './VbNamesInput.vue'

const props = defineProps({ activityId: { type: String, required: true },
                            act: { type: Object, default: null } })
const emit = defineEmits(['changed', 'status-changed'])
// 志愿北京对接部门的实际名称（登录信息里带下来），文案里别写死部门名
const vbDept = computed(() => auth.user?.vb_dept_name || '校青协')

const VBC_TAG = { pending: 'warning', confirmed: 'success', returned: 'danger', void: 'info' }
const REQ_TAG = { pending: 'warning', approved: 'success', rejected: 'danger' }
const EVENT_TYPE = { approve: 'success', reject: 'danger', submit: 'primary' }
const approval = ref(null)
const hours = ref(null)
const loading = ref(false)
const files = ref(null)
const vbc = ref({ names: [], items: [], can_edit: false, can_resubmit: false,
                  can_repush: false, can_decide: false, edit_block: '', handler: '' })
const draftNames = ref([])
const vbBusy = ref(false)
const repushReason = ref('')
const dc = ref({ visible: false, approve: true, row: null })
// 时长审核要附的两份盖章材料；template 是对应空白模板的格子名（盖章立项书照立项书模板填）
const STAMPED = [{ kind: 'stamped', label: '盖章立项书（即盖章项目申请书）', template: 'proposal' },
                 { kind: 'closing', label: '盖章结项书（即盖章时长认证明细）', template: 'closing' }]
const docTemplates = ref(null)   // vuecheck: script-only，模板只通过 templates 读它
const templates = computed(() => Object.fromEntries(
  (docTemplates.value?.items || []).map(x => [x.slot, x.file])))

async function load() {
  loading.value = true
  try {
    const base = `/api/staff/activities/${props.activityId}`
    ;[files.value, vbc.value, approval.value, hours.value, docTemplates.value] = await Promise.all([
      api.get(`${base}/project-files`), api.get(`${base}/vb-confirms`),
      api.get(`${base}/approval`, { silent: true }).catch(() => null),
      api.get(`${base}/hours-requests`, { silent: true }).catch(() => null),
      api.get('/api/staff/doc-templates', { silent: true }).catch(() => null)])
    draftNames.value = [...vbc.value.names]
    emit('changed', files.value)
  } finally { loading.value = false }
}
onMounted(load)

async function saveNames() {
  if (!draftNames.value.length) return ElMessage.warning('请填写志愿北京上的活动名称')
  vbBusy.value = true
  try {
    await api.put(`/api/staff/activities/${props.activityId}/vb-names`,
                  { names: draftNames.value })
    ElMessage.success('已保存')
    load()
  } finally { vbBusy.value = false }
}

async function resubmit() {
  if (!draftNames.value.length) return ElMessage.warning('请填写志愿北京上的活动名称')
  vbBusy.value = true
  try {
    await api.post(`/api/staff/activities/${props.activityId}/vb-confirms/resubmit`,
                   { names: draftNames.value })
    ElMessage.success(`已重新提交，等${vbDept.value}确认`)
    load()
  } finally { vbBusy.value = false }
}

async function repush() {
  if (!draftNames.value.length) return ElMessage.warning('请填写志愿北京上的活动名称')
  if (!repushReason.value.trim()) return ElMessage.warning('请填写再次推送的原因，外联会看到')
  vbBusy.value = true
  try {
    await api.post(`/api/staff/activities/${props.activityId}/vb-confirms/repush`,
                   { names: draftNames.value, reason: repushReason.value })
    repushReason.value = ''
    ElMessage.success(`已再次推送，等${vbDept.value}确认`)
    load()
  } finally { vbBusy.value = false }
}

function openDecide(row, approve) {
  dc.value = { visible: true, approve, row }
}

async function submitDecide(reply) {
  const { row, approve } = dc.value
  await api.post(`/api/staff/vb-confirms/${row.id}/decide`, { approve, reply })
  ElMessage.success(approve ? '已确认' : '已退回并通知管理者')
  emit('status-changed')
  load()
}

defineExpose({ load })
</script>

<style scoped>
.submit-names { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
</style>
