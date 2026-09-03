<template>
  <div class="page">
    <h2>活动审批</h2>
    <el-alert type="info" :closable="false" style="margin-bottom:12px">
      所有活动发布前都要过校青协。通过后活动回到<b>发起方</b>手里，由他自己点发布；
      驳回必须写理由。不该由你批的可以<b>转交</b>，转交要写理由，三方都会收到通知。
    </el-alert>

    <el-empty v-if="!rows.length && !loading" :image-size="70"
              description="没有待你审批的活动" />

    <el-card v-for="a in rows" :key="a.id" shadow="never" class="item">
      <div class="head">
        <b class="title" @click="$router.push('/activity/' + a.id)">{{ a.title }}</b>
        <el-tag size="small" effect="plain">{{ a.recruit_label }}</el-tag>
        <el-tag v-if="!a.mine" size="small" type="info" effect="plain">
          当前审批人：{{ a.assignee }}
        </el-tag>
        <span style="margin-left:auto">
          <el-button size="small" type="success" @click="decide(a, true)">通过</el-button>
          <el-button size="small" type="danger" plain @click="decide(a, false)">驳回</el-button>
          <el-button size="small" plain @click="openTransfer(a)">转交</el-button>
        </span>
      </div>
      <el-descriptions :column="narrow ? 1 : 3" size="small" border
                       style="margin-top:8px">
        <el-descriptions-item label="组织单位">{{ a.organizer || '—' }}</el-descriptions-item>
        <el-descriptions-item label="归口部门">{{ a.department }}</el-descriptions-item>
        <el-descriptions-item label="提交人">{{ a.creator }}</el-descriptions-item>
        <el-descriptions-item label="活动时间">{{ a.time_text }}</el-descriptions-item>
        <el-descriptions-item label="地点">{{ a.location || '待定' }}</el-descriptions-item>
        <el-descriptions-item label="场次数">{{ a.shifts_count }}</el-descriptions-item>
      </el-descriptions>
      <!-- 提交人写的说明。审批人就是在这一屏点通过/驳回的，这句话必须在这里 -->
      <div class="submit-note">
        <b>提交说明</b>
        <span :class="{ dim: !a.submit_note }">{{ a.submit_note || '提交时未填写' }}</span>
      </div>
      <div class="dim" style="margin-top:6px">
        提交于 {{ fmtDT(a.submitted_at) }} ·
        <el-link type="primary" :underline="false"
                 @click="$router.push('/activity/' + a.id)">查看完整安排</el-link>
      </div>
    </el-card>

    <!-- 部门与部长：换届时管理员在这里改 -->
    <template v-if="isAdmin">
      <div class="section-title" style="margin-top:20px">部门与部长</div>
      <el-alert type="info" :closable="false" style="margin-bottom:10px">
        活动归口到哪个部门，就由那个部门的部长审。<b>没有部长的部门无法被选为归口部门</b>，
        换届后记得来这里改。会长团可以接管任何一条审批。
      </el-alert>
      <el-table :data="depts" size="small">
        <el-table-column prop="name" label="部门" min-width="140" />
        <el-table-column prop="note" label="说明" min-width="200" />
        <el-table-column label="部长" width="140">
          <template #default="{ row }">
            <span v-if="row.head_name">{{ row.head_name }}</span>
            <el-tag v-else size="small" type="warning" effect="plain">未指定</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button size="small" link type="primary"
                       @click="openHead(row)">指定部长</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <el-dialog v-model="hd.visible" title="指定部长" width="420">
      <p class="dim" style="margin-top:0">{{ hd.row?.name }}</p>
      <el-input v-model="hd.studentId" placeholder="青协成员的学号；留空 = 清空部长" />
      <template #footer>
        <el-button @click="hd.visible = false">取消</el-button>
        <el-button type="primary" :loading="hd.busy" @click="saveHead">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dlg.visible" :title="dlg.approve ? '通过审批' : '驳回'"
               width="480">
      <p class="dim" style="margin-top:0">{{ dlg.row?.title }}</p>
      <el-input v-model="dlg.comment" type="textarea" :rows="3" maxlength="500"
                show-word-limit
                :placeholder="dlg.approve ? '审批意见（选填），会发给发起方'
                                          : '请写明驳回理由，发起方才知道改什么'" />
      <template #footer>
        <el-button @click="dlg.visible = false">取消</el-button>
        <el-button :type="dlg.approve ? 'success' : 'danger'" :loading="dlg.busy"
                   @click="submitDecision">
          确认{{ dlg.approve ? '通过' : '驳回' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="tr.visible" title="转交审批" width="480">
      <p class="dim" style="margin-top:0">{{ tr.row?.title }}</p>
      <el-form label-width="88px">
        <el-form-item label="转交给">
          <el-select v-model="tr.deptId" placeholder="选择部门（转给该部门部长，活动归口随之改为该部门）"
                     style="width:100%" clearable>
            <el-option v-for="d in depts" :key="d.id" :value="d.id"
                       :label="d.name + (d.head_name ? `（${d.head_name}）` : '（未设部长）')"
                       :disabled="!d.has_head" />
          </el-select>
        </el-form-item>
        <el-form-item label="或指定人">
          <el-input v-model="tr.studentId" placeholder="输入青协成员的学号（优先于部门）" />
        </el-form-item>
        <el-form-item label="理由" required>
          <el-input v-model="tr.reason" type="textarea" :rows="2"
                    placeholder="如：该活动属于支教类，应由支教项目部归口" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="tr.visible = false">取消</el-button>
        <el-button type="primary" :loading="tr.busy" @click="submitTransfer">转交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import { auth } from '../store'

const narrow = useNarrow()
const rows = ref([])
const depts = ref([])
const loading = ref(false)
const dlg = ref({ visible: false, row: null, approve: true, comment: '', busy: false })
const hd = ref({ visible: false, row: null, studentId: '', busy: false })
// 部门与部长的维护：管理员或会长团
const isAdmin = computed(() =>
  auth.activeIdentity === 'admin'
  || (auth.activeIdentity === 'qingxie' && auth.user?.qingxie_post === 'council'))

function openHead(row) {
  hd.value = { visible: true, row, studentId: '', busy: false }
}

async function saveHead() {
  hd.value.busy = true
  try {
    await api.patch(`/api/staff/departments/${hd.value.row.id}`,
                    { head_student_id: hd.value.studentId.trim() })
    ElMessage.success('已保存')
    hd.value.visible = false
    load()
  } finally { hd.value.busy = false }
}
const tr = ref({ visible: false, row: null, deptId: '', studentId: '', reason: '', busy: false })

async function load() {
  loading.value = true
  try {
    rows.value = await api.get('/api/staff/approvals')
    depts.value = await api.get('/api/staff/departments')
  } finally { loading.value = false }
}
onMounted(load)

function decide(row, approve) {
  dlg.value = { visible: true, row, approve, comment: '', busy: false }
}

async function submitDecision() {
  const d = dlg.value
  if (!d.approve && d.comment.trim().length < 2) {
    return ElMessage.warning('驳回必须写明理由')
  }
  d.busy = true
  try {
    await api.post(`/api/staff/activities/${d.row.id}/decide`,
                   { approve: d.approve, comment: d.comment })
    ElMessage.success(d.approve ? '已通过，发起方可以自行发布' : '已驳回并通知发起方')
    d.visible = false
    load()
  } finally { d.busy = false }
}

function openTransfer(row) {
  tr.value = { visible: true, row, deptId: '', studentId: '', reason: '', busy: false }
}

async function submitTransfer() {
  const t = tr.value
  if (!t.deptId && !t.studentId.trim()) return ElMessage.warning('请选择部门或指定人')
  if (t.reason.trim().length < 2) return ElMessage.warning('请写明转交理由')
  t.busy = true
  try {
    const d = await api.post(`/api/staff/activities/${t.row.id}/transfer-approval`,
      { department_id: t.deptId, student_id: t.studentId, reason: t.reason })
    ElMessage.success(`已转交给 ${d.assignee}`)
    t.visible = false
    load()
  } finally { t.busy = false }
}
</script>

<style scoped>
.submit-note {
  margin-top: 8px; padding: 8px 12px; border-radius: 8px;
  background: var(--el-fill-color-light); font-size: 13px; line-height: 1.75;
}
.submit-note b { margin-right: 8px; }
.item { margin-bottom: 12px; border-radius: 12px; }
.head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.title { font-size: 15px; color: var(--el-color-primary); cursor: pointer; }
</style>
