<template>
  <div class="page">
    <h2>活动审批</h2>
    <el-alert type="info" :closable="false">
      通过可以附意见（选填），驳回、退回与转交须写明理由；意见随通知发给申请人，审核页签里也看得到。
    </el-alert>

    <!-- 值班：对外联络部的审批通知只发给值班的人，没人值班才发部长。
         部长在这里指定，在班的人在这里交班 -->
    <el-card v-for="d in duties" :key="d.dept_id" shadow="never" class="item duty">
      <div class="head">
        <b>{{ d.dept_name }} · 值班</b>
        <el-tag v-if="d.on_duty" type="success" effect="plain">
          当前值班：{{ d.on_duty.name }}（{{ d.on_duty.post_label }}）
        </el-tag>
        <el-tag v-else type="warning" effect="plain">
          无人值班，审批通知发给部长{{ d.head ? ' ' + d.head.name : '' }}
        </el-tag>
        <el-link v-if="d.history.length" type="primary" underline="never"
                 style="margin-left:auto" @click="d.showHistory = !d.showHistory">
          {{ d.showHistory ? '收起交接记录' : `交接记录（${d.history.length}）` }}
        </el-link>
      </div>
      <div class="dim" style="margin-top:8px">
        本部门的活动审批、时长锁定/解锁与志愿北京外联确认由值班人处理，部长随时可以直接处理；
        部长每晚 21 点收到当天的汇总。
      </div>
      <div v-if="d.can_assign || d.can_handover" class="toolbar" style="margin:8px 0 0">
        <el-select v-model="d.pick" :placeholder="d.can_assign ? '选择值班人' : '交班给'"
                   clearable style="width:220px">
          <el-option v-for="u in d.eligible" :key="u.id" :value="u.id"
                     :disabled="u.id === d.on_duty?.id"
                     :label="`${u.name}（${u.post_label}）`" />
        </el-select>
        <el-input v-model="d.note" placeholder="备注（选填）" style="width:200px" />
        <el-button type="primary" :loading="d.busy" @click="setDuty(d)">
          {{ d.can_assign ? '指定值班' : '交班' }}
        </el-button>
        <el-popconfirm v-if="d.can_assign && d.on_duty" title="撤下值班后，审批通知改发部长。确定？"
                       @confirm="setDuty(d, true)">
          <template #reference><el-button plain>撤下值班</el-button></template>
        </el-popconfirm>
      </div>
      <el-table v-if="d.showHistory" :data="d.history" size="small" style="margin-top:8px">
        <el-table-column label="时间" width="120">
          <template #default="{ row }">{{ fmtDT(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="交接" min-width="160">
          <template #default="{ row }">{{ row.from || '无人' }} → {{ row.to || '无人值班' }}</template>
        </el-table-column>
        <el-table-column prop="actor" label="操作人" width="100" />
        <el-table-column prop="note" label="备注" min-width="140" />
      </el-table>
    </el-card>

    <!-- 立项书、结项书模板：外联部长在这里上传和更换，发起方在「活动管理」页下载 -->
    <el-card v-if="docTemplates?.can_manage" shadow="never" class="item">
      <div class="head"><b>立项书、结项书模板</b></div>
      <div class="dim" style="margin-top:8px">
        发起方在「活动管理」页右上角的「材料模板」下载，提交立项书、盖章材料时旁边也有下载链接。
      </div>
      <ProjectFileSlot v-for="t in docTemplates.items" :key="t.slot" :kind="t.slot"
                       :label="t.label" :file="t.file" :can-upload="true"
                       :exts="docTemplates.exts" :upload-url="`/api/staff/doc-templates/${t.slot}`"
                       @changed="loadTemplates" />
    </el-card>

    <!-- 志愿北京外联确认：时长锁定后管理者填的名称推到这里（不论活动归口哪个部门），
         外联上志愿北京核对，确认即活动完成；退回写理由，管理者改好名称重提 -->
    <template v-if="vbConfirms.length">
      <div class="section-title">志愿北京待确认</div>
      <el-card v-for="v in vbConfirms" :key="v.id" shadow="never" class="item">
        <div class="head">
          <b class="title" @click="$router.push('/manage/' + v.activity_id + '?tab=review')">
            {{ v.activity_title }}
          </b>
          <el-tag v-if="v.kind === 'resubmit'" size="small" type="warning" effect="plain">
            退回后重提
          </el-tag>
          <span style="margin-left:auto">
            <el-button size="small" type="success" @click="openVb(v, true)">确认</el-button>
            <el-button size="small" type="danger" plain @click="openVb(v, false)">退回</el-button>
          </span>
        </div>
        <div class="submit-note">
          <b>志愿北京活动名称</b>
          <div v-for="n in v.names" :key="n">{{ n }}</div>
        </div>
        <div v-if="v.last_return" class="submit-note">
          <b>上次退回理由</b><span class="pre">{{ v.last_return }}</span>
        </div>
        <div class="dim" style="margin-top:8px">
          {{ v.organizer }} · 时长锁定于 {{ fmtDT(v.locked_at) }} ·
          {{ v.submitted_by }} {{ v.kind === 'resubmit' ? '重提' : '推送' }}于 {{ fmtDT(v.submitted_at) }}
          · 在志愿北京核对后确认，活动即标记为已完成
        </div>
      </el-card>
    </template>

    <!-- 时长的两道审批：锁定（批的是这场活动的账能不能结）与解锁（结完还要改）。
         批的都是「能不能」，改成多少仍由管理者填 -->
    <template v-if="hoursReqs.length">
      <div class="section-title">时长审批</div>
      <el-card v-for="u in hoursReqs" :key="u.id" shadow="never" class="item">
        <div class="head">
          <b class="title" @click="$router.push('/manage/' + u.activity_id + '?tab=hours')">
            {{ u.activity_title }}
          </b>
          <el-tag size="small" :type="u.kind === 'lock' ? 'primary' : 'warning'"
                  effect="plain">
            {{ u.kind === 'lock' ? '时长待审核' : '申请解锁时长' }}
          </el-tag>
          <span style="margin-left:auto">
            <el-button size="small" type="success" @click="decideHours(u, true)">同意</el-button>
            <el-button size="small" type="danger" plain
                       @click="decideHours(u, false)">驳回</el-button>
          </span>
        </div>
        <div class="submit-note">
          <b>说明</b><span class="pre">{{ u.reason }}</span>
        </div>
        <div v-if="u.vb_names?.length" class="submit-note">
          <b>志愿北京活动名称</b>
          <div v-for="n in u.vb_names" :key="n">{{ n }}</div>
        </div>
        <!-- 盖章材料就在这里看：审批人多半不是这场活动的管理者，进不了活动管理页 -->
        <div v-if="u.files?.length" class="submit-note">
          <b>盖章材料</b>
          <div v-for="f in u.files" :key="f.id">
            {{ f.label }}：<el-link type="primary" underline="never"
                                   @click="download(`/api/materials/${f.id}`)">{{ f.title }}</el-link>
          </div>
        </div>
        <div class="dim" style="margin-top:8px">
          {{ u.requested_by }} 提交于 {{ fmtDT(u.created_at) }} ·
          {{ u.kind === 'lock' ? '通过即锁定时长，志愿北京名称推给对外联络部确认；驳回退回管理者修改'
                               : '通过后解锁，外联确认作废，管理者改完须重新核准' }}
        </div>
      </el-card>
    </template>
    <div v-if="hoursReqs.length || vbConfirms.length" class="section-title">待审批活动</div>

    <el-empty v-if="!rows.length && !loading"
              :description="duties.length && !duties.some(d => d.can_handover || d.can_assign)
                ? '没有待你审批的活动（轮到你值班时，本部门的审批会出现在这里）'
                : '没有待你审批的活动'" />

    <el-card v-for="a in rows" :key="a.id" shadow="never" class="item">
      <div class="head">
        <b class="title" @click="$router.push('/activity/' + a.id)">{{ a.title }}</b>
        <el-tag size="small" effect="plain">{{ a.recruit_label }}</el-tag>
        <el-tag v-if="!a.mine" size="small" type="info" effect="plain">
          当前审批人：{{ a.assignee }}
        </el-tag>
        <el-tag v-if="a.on_duty" size="small" type="success" effect="plain">
          值班：{{ a.on_duty }}
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
        <el-descriptions-item v-if="a.record_only" label="说明">
          线下已办的补记录活动：不公开、不报名，通过后由发起方导入名单补记时长
        </el-descriptions-item>
        <el-descriptions-item v-else label="场次数">{{ a.shifts_count }}</el-descriptions-item>
      </el-descriptions>
      <!-- 提交人写的说明。审批人就是在这一屏点通过/驳回的，这句话必须在这里 -->
      <div class="submit-note">
        <b>提交说明</b>
        <span class="pre" :class="{ dim: !a.submit_note }">{{ a.submit_note || '提交时未填写' }}</span>
      </div>
      <!-- 对外活动的立项书：批的就是它 -->
      <div v-if="a.external" class="submit-note">
        <b>立项书</b>
        <el-link v-if="a.proposal" type="primary" underline="never"
                 @click="download(`/api/materials/${a.proposal.id}`)">{{ a.proposal.title }}</el-link>
        <el-tag v-else size="small" type="danger" effect="plain">未上传（不能通过，请驳回让发起方补传）</el-tag>
      </div>
      <div class="dim" style="margin-top:8px">
        提交于 {{ fmtDT(a.submitted_at) }} ·
        <el-link type="primary" underline="never"
                 @click="$router.push('/activity/' + a.id)">查看完整安排</el-link>
      </div>
    </el-card>

    <!-- 部门与部长：换届、增设或撤销部门时管理员在这里改 -->
    <template v-if="isAdmin">
      <div class="section-title" style="margin-top:16px">
        部门与部长
        <el-button size="small" type="primary" plain @click="openDept(null)">新增部门</el-button>
      </div>
      <el-alert type="info" :closable="false">
        活动由归口部门的部长审批，<b>没有部长的部门无法被选为归口部门</b>，
        换届后请及时调整。会长团可以接管任何一条审批。
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
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button size="small" link type="primary"
                       @click="openDept(row)">编辑</el-button>
            <el-button size="small" link type="danger"
                       @click="removeDept(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <el-dialog v-model="dd.visible" :title="dd.row ? '编辑部门' : '新增部门'" width="420">
      <el-form label-position="top">
        <el-form-item label="部门名称">
          <el-input v-model="dd.name" placeholder="如：社区服务部" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="dd.note" placeholder="选填，显示在归口部门下拉里" />
        </el-form-item>
        <el-form-item v-if="dd.row" label="部长">
          <el-input v-model="dd.studentId"
                    :placeholder="dd.row.head_name
                                  ? `现任 ${dd.row.head_name}，填新学号即换人，留空不动`
                                  : '青协成员的学号，暂不指定可留空'" />
        </el-form-item>
      </el-form>
      <p v-if="!dd.row" class="dim" style="margin:0">
        新部门要先指定部长才能作为归口部门，建好后在这一行「编辑」里填。
      </p>
      <template #footer>
        <el-button @click="dd.visible = false">取消</el-button>
        <el-button type="primary" :loading="dd.busy" @click="saveDept">保存</el-button>
      </template>
    </el-dialog>

    <!-- 三种审批共用一个意见对话框：通过选填、驳回/退回必填 -->
    <ApprovalCommentDialog v-model="cd.visible" :approve="cd.approve" :title="cd.title"
                           :subject="cd.subject" :audience="cd.audience"
                           :approve-label="cd.approveLabel" :reject-label="cd.rejectLabel"
                           :templates="cd.templates" :submit="submitDecision" />

    <el-dialog v-model="tr.visible" title="转交审批" width="480">
      <p class="dim">{{ tr.row?.title }}</p>
      <el-form label-width="88px">
        <el-form-item label="转交给">
          <!-- 转交时最需要知道的就是「这个部门管什么」，所以备注要露出来。
               它可能有好几行，换行的样式在 styles.css 的 .dept-popper 里 -->
          <el-select v-model="tr.deptId" placeholder="选择部门（活动归口随之改为该部门）"
                     popper-class="dept-popper" style="width:100%" clearable>
            <el-option v-for="d in depts" :key="d.id" :value="d.id"
                       :label="d.name + (d.head_name ? `（部长 ${d.head_name}）` : '（未设部长）')"
                       :disabled="!d.has_head">
              <div class="dept-opt">
                <div>
                  {{ d.name }}
                  <span class="dim">
                    {{ d.head_name ? `部长 ${d.head_name}` : '未设部长' }}
                  </span>
                </div>
                <div v-if="d.note" class="dept-opt-note">{{ d.note }}</div>
              </div>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="或指定人">
          <el-input v-model="tr.studentId" placeholder="青协成员的学号（优先于部门）" />
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, download, fmtDT } from '../api'
import ApprovalCommentDialog from '../components/ApprovalCommentDialog.vue'
import ProjectFileSlot from '../components/ProjectFileSlot.vue'
import { closingApproved, projectApproved, vbConfirmed } from '../replyTemplates'
import { useNarrow } from '../composables/useNarrow'
import { auth } from '../store'

const narrow = useNarrow()
const rows = ref([])
const hoursReqs = ref([])
const vbConfirms = ref([])
const duties = ref([])
const depts = ref([])
const loading = ref(false)
// 审批意见对话框：kind = activity 活动立项 / hours 时长锁定解锁 / vb 外联确认志愿北京
const cd = ref({ visible: false, kind: '', row: null, approve: true, title: '', subject: '',
                 audience: '', approveLabel: '通过', rejectLabel: '驳回', templates: [] })
// 部门的维护：管理员或会长团
const dd = ref({ visible: false, row: null, name: '', note: '', studentId: '', busy: false })
const isAdmin = computed(() =>
  auth.activeIdentity === 'admin'
  || (auth.activeIdentity === 'qingxie' && auth.user?.qingxie_post === 'council'))

function openDept(row) {
  dd.value = { visible: true, row, name: row?.name || '', note: row?.note || '',
               studentId: '', busy: false }
}

async function saveDept() {
  const d = dd.value
  if (!d.name.trim()) return ElMessage.warning('请填写部门名称')
  d.busy = true
  try {
    if (d.row) {
      // 部长这一格留空表示「不动」，清空部长请在角色管理里改这个人的层级
      const body = { name: d.name.trim(), note: d.note.trim() }
      if (d.studentId.trim()) body.head_student_id = d.studentId.trim()
      await api.patch(`/api/staff/departments/${d.row.id}`, body)
    } else {
      await api.post('/api/staff/departments',
                     { name: d.name.trim(), note: d.note.trim() })
    }
    ElMessage.success('已保存')
    d.visible = false
    load()
  } finally { d.busy = false }
}

async function removeDept(row) {
  try {
    await ElMessageBox.confirm(`确定删除「${row.name}」？名下还有人或还挂着活动时不能删。`,
                               '删除部门', { type: 'warning' })
  } catch { return }
  await api.del(`/api/staff/departments/${row.id}`)
  ElMessage.success('已删除')
  load()
}
const tr = ref({ visible: false, row: null, deptId: '', studentId: '', reason: '', busy: false })

async function load() {
  loading.value = true
  try {
    rows.value = await api.get('/api/staff/approvals')
    hoursReqs.value = await api.get('/api/staff/hours-requests')
    vbConfirms.value = await api.get('/api/staff/vb-confirms')
    depts.value = await api.get('/api/staff/departments')
    duties.value = (await api.get('/api/staff/duty')).map(withForm)
  } finally { loading.value = false }
  loadTemplates()
}
onMounted(load)

const docTemplates = ref(null)
async function loadTemplates() {
  docTemplates.value = await api.get('/api/staff/doc-templates', { silent: true })
    .catch(() => null)
}

const withForm = d => ({ ...d, pick: '', note: '', busy: false, showHistory: false })

/** 指定 / 交班 / 撤下值班（clear=true）。 */
async function setDuty(d, clear = false) {
  if (!clear && !d.pick) return ElMessage.warning(d.can_assign ? '请选择值班人' : '请选择交班给谁')
  d.busy = true
  try {
    const r = await api.post(`/api/staff/duty/${d.dept_id}`,
                             { user_id: clear ? '' : d.pick, note: d.note })
    ElMessage.success(r.on_duty ? `现在由 ${r.on_duty.name} 值班` : '已撤下值班')
    // 交班之后自己可能就不在班了，列表要跟着刷新
    load()
  } finally { d.busy = false }
}

/** 外联确认志愿北京：确认即活动完成；退回通知管理者改名称重提（时长保持锁定）。 */
function openVb(row, approve) {
  cd.value = { visible: true, kind: 'vb', row, approve, subject: row.activity_title,
               title: approve ? '确认志愿北京' : '退回志愿北京名称',
               audience: '活动管理者', approveLabel: '确认', rejectLabel: '退回',
               templates: [vbConfirmed(row.names || [])] }
}

/** 锁定：同意即封账，志愿北京名称推给对外联络部确认；驳回退回管理者改。
 *  解锁：同意即允许改已经封了的账，外联确认作废，改完他要重新核准。 */
function decideHours(row, approve) {
  const what = row.kind === 'lock' ? '时长审核' : '解锁申请'
  cd.value = { visible: true, kind: 'hours', row, approve, subject: row.activity_title,
               title: `${approve ? '同意' : '驳回'}${what}`, audience: '申请人',
               approveLabel: '同意', rejectLabel: '驳回',
               templates: row.kind === 'lock' ? [closingApproved(row.activity_title)] : [] }
}

function decide(row, approve) {
  cd.value = { visible: true, kind: 'activity', row, approve, subject: row.title,
               title: approve ? '通过审批' : '驳回', audience: '发起方',
               approveLabel: '通过', rejectLabel: '驳回', templates: [projectApproved(row.title)] }
}

async function submitDecision(comment) {
  const { kind, row, approve } = cd.value
  if (kind === 'activity') {
    await api.post(`/api/staff/activities/${row.id}/decide`, { approve, comment })
    ElMessage.success(approve ? '已通过，发起方可以自行发布' : '已驳回并通知发起方')
  } else if (kind === 'hours') {
    await api.post(`/api/staff/hours-requests/${row.id}/decide`, { approve, reply: comment })
    ElMessage.success(approve
      ? (row.kind === 'lock' ? '已通过，时长已锁定，名称已推给对外联络部' : '已同意解锁')
      : '已驳回')
  } else {
    await api.post(`/api/staff/vb-confirms/${row.id}/decide`, { approve, reply: comment })
    ElMessage.success(approve ? '已确认，活动已完成' : '已退回并通知管理者')
  }
  load()
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
  background: var(--el-fill-color-light); font-size: 13px; line-height: 1.6;
}
.submit-note b { margin-right: 8px; }
.item { margin-bottom: 12px; border-radius: 12px; }
.head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.title { font-size: 15px; color: var(--el-color-primary); cursor: pointer; }
</style>
