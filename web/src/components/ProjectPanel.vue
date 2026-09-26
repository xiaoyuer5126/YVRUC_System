<!-- 活动管理「立项与登记」页签：立项书、盖章立项书、志愿北京登记。
     三样都是对外联络部要看的东西，放在一处，管理者不用到处找。 -->
<template>
  <div v-loading="loading">
    <el-card shadow="never">
      <template #header><b>立项材料</b></template>
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
        <template v-if="files.external">
          <el-divider style="margin:8px 0" />
          <ProjectFileSlot :activity-id="activityId" kind="stamped" label="盖章立项书"
                           :file="files.stamped" :can-upload="files.can_upload_stamped"
                           :block="files.manager ? files.stamped_block : ''"
                           :required="!files.stamped"
                           hint="对外活动的时长须凭盖章立项书报校青协审核；时长锁定前可随时更换"
                           @changed="load" />
        </template>
      </template>
    </el-card>

    <el-card shadow="never" style="margin-top:12px">
      <template #header>
        <b>志愿北京登记</b>
        <span class="dim" style="margin-left:8px">
          在志愿北京建好对应活动后在这里登记名称，由对外联络部核对
        </span>
      </template>
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

const props = defineProps({ activityId: { type: String, required: true } })
const emit = defineEmits(['changed'])

const VB_TAG = { submitted: 'warning', approved: 'success', rejected: 'danger', void: 'info' }
const loading = ref(false)
const files = ref(null)
const vb = ref({ items: [], can_submit: false, can_decide: false })
const vbName = ref('')
const vbBusy = ref(false)

async function load() {
  loading.value = true
  try {
    const base = `/api/staff/activities/${props.activityId}`
    ;[files.value, vb.value] = await Promise.all([
      api.get(`${base}/project-files`), api.get(`${base}/vb-registrations`)])
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
