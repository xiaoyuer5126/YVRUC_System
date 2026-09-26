<!-- 活动管理「审核」页签：这场活动要过的三道审核放在一处看。
     立项审核（发布前）→ 时长审核（结项）→ 志愿北京项目审核。
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
          <div v-if="e.comment" class="dim">{{ e.comment }}</div>
        </el-timeline-item>
      </el-timeline>
      <div v-else class="dim" style="margin-bottom:8px">还没有提交过审批（在页头点「提交审批」）。</div>
      <el-alert v-if="files && !files.external" type="info" :closable="false"
                style="margin-bottom:8px">
        提交<b>对外联络部</b>审批的活动（二级志愿团体的活动）须上传立项书；
        审批通过后立项书不能再更换，被驳回则作废，重新提交时要传新的一份。
      </el-alert>
      <template v-if="files">
        <ProjectFileSlot :activity-id="activityId" kind="proposal" label="立项书"
                         :file="files.proposal" :can-upload="files.can_upload_proposal"
                         :block="files.manager ? files.proposal_block : ''"
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
        <ProjectFileSlot :activity-id="activityId" kind="stamped" label="盖章立项书"
                         :file="files.stamped" :can-upload="files.can_upload_stamped"
                         :block="files.manager ? files.stamped_block : ''"
                         :required="!files.stamped"
                         hint="对外活动提交时长审核必须附盖章立项书；活动发布后、时长锁定前可随时更换"
                         @changed="load" />
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
              <div v-if="row.reply" class="dim">意见：{{ row.reply }}</div>
            </template>
            <span v-else class="dim">等待审核</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ③ 志愿北京项目审核 -->
    <el-card shadow="never" style="margin-top:12px">
      <template #header>
        <b>志愿北京项目审核</b>
        <span class="dim" style="margin-left:8px">
          在志愿北京建好对应活动后在这里登记名称，由对外联络部核对
        </span>
      </template>
      <div v-if="vb.manager && !vb.published" class="dim">
        活动正式发布之后才能登记（先立项、再结项）。
      </div>
      <div v-if="vb.can_submit" class="toolbar">
        <el-input v-model="vbName" maxlength="200" style="flex:1 1 260px"
                  placeholder="志愿北京上的活动名称" @keyup.enter="submitVb" />
        <el-button type="primary" :loading="vbBusy" @click="submitVb">登记</el-button>
      </div>
      <el-table :data="vb.items" size="small" empty-text="还没有登记" style="margin-top:8px">
        <el-table-column prop="vb_name" label="志愿北京活动名称" min-width="200" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="VB_TAG[row.status]">
              {{ row.status_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="登记" width="150">
          <template #default="{ row }">
            {{ row.submitted_by }}<div class="dim">{{ fmtDT(row.submitted_at) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="处理" min-width="170">
          <template #default="{ row }">
            <template v-if="row.handled_by">
              {{ row.handled_by }} · {{ fmtDT(row.handled_at) }}
              <div v-if="row.reply" class="dim">{{ row.reply }}</div>
            </template>
            <span v-else class="dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <template v-if="row.status === 'submitted' && vb.can_decide">
              <el-button size="small" link type="success" @click="decide(row, true)">通过</el-button>
              <el-button size="small" link type="danger" @click="decide(row, false)">驳回</el-button>
            </template>
            <el-popconfirm v-if="vb.can_submit && ['submitted', 'approved'].includes(row.status)"
                           title="作废这条登记？作废后可以重新登记" @confirm="voidVb(row)">
              <template #reference>
                <el-button size="small" link>作废</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, fmtDT } from '../api'
import ProjectFileSlot from './ProjectFileSlot.vue'

const props = defineProps({ activityId: { type: String, required: true },
                            act: { type: Object, default: null } })
const emit = defineEmits(['changed'])

const VB_TAG = { submitted: 'warning', approved: 'success', rejected: 'danger', void: 'info' }
const REQ_TAG = { pending: 'warning', approved: 'success', rejected: 'danger' }
const EVENT_TYPE = { approve: 'success', reject: 'danger', submit: 'primary' }
const approval = ref(null)
const hours = ref(null)
const loading = ref(false)
const files = ref(null)
const vb = ref({ items: [], can_submit: false, can_decide: false,
                 manager: false, published: false })
const vbName = ref('')
const vbBusy = ref(false)

async function load() {
  loading.value = true
  try {
    const base = `/api/staff/activities/${props.activityId}`
    ;[files.value, vb.value, approval.value, hours.value] = await Promise.all([
      api.get(`${base}/project-files`), api.get(`${base}/vb-registrations`),
      api.get(`${base}/approval`, { silent: true }).catch(() => null),
      api.get(`${base}/hours-requests`, { silent: true }).catch(() => null)])
    emit('changed', files.value)
  } finally { loading.value = false }
}
onMounted(load)

async function submitVb() {
  if (vbName.value.trim().length < 2) return ElMessage.warning('请填写志愿北京上的活动名称')
  vbBusy.value = true
  try {
    await api.post(`/api/staff/activities/${props.activityId}/vb-registrations`,
                   { vb_name: vbName.value.trim() })
    ElMessage.success('已登记，等对外联络部核对')
    vbName.value = ''
    load()
  } finally { vbBusy.value = false }
}

async function voidVb(row) {
  await api.post(`/api/staff/vb-registrations/${row.id}/void`)
  ElMessage.success('已作废')
  load()
}

async function decide(row, approve) {
  let reply = ''
  if (!approve) {
    const r = await ElMessageBox.prompt('写明为什么没通过，会发给登记人。', '驳回登记',
                                        { inputValidator: v => (v || '').trim().length >= 2
                                                               || '至少 2 个字' })
    reply = r.value.trim()
  }
  await api.post(`/api/staff/vb-registrations/${row.id}/decide`, { approve, reply })
  ElMessage.success(approve ? '已通过' : '已驳回')
  load()
}

defineExpose({ load })
</script>
