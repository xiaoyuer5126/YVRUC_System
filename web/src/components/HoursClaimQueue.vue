<!-- 校外时长补登的审批队列：志愿北京对接部门核对证明材料后通过或驳回。
     审批人是「外联那摊事」的同一批（值班人/部长 + 会长团/管理员），见后端
     liaison.vb_decider；别的能进审批页的人后端回空表，这块整个不显示。
     通过可核定一个不一样的时长，已通过的能撤销；自己的补登要由别人批、别人撤。 -->
<template>
  <el-card v-if="canDecide" shadow="never" style="margin-top:16px">
    <template #header>
      <b>校外时长补登</b>
      <el-tag v-if="pending" type="danger" size="small" style="margin-left:8px">
        {{ pending }} 条待审批
      </el-tag>
      <span class="dim" style="margin-left:8px">
        志愿者在校外自行报名参加的志愿活动，核对证明材料后审批，通过即计入时长
      </span>
    </template>

    <div class="toolbar">
      <el-radio-group v-model="status" @change="reload">
        <el-radio-button value="pending">待审批</el-radio-button>
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="approved">已通过</el-radio-button>
        <el-radio-button value="rejected">已驳回</el-radio-button>
        <el-radio-button value="revoked">已撤销</el-radio-button>
      </el-radio-group>
      <el-button type="primary" plain @click="exportAll">导出补登表</el-button>
      <span class="dim">导出的表是录志愿北京用的，一条一行，含身份证号，下载留痕</span>
    </div>

    <el-table v-loading="loading" :data="items" empty-text="没有这一类的补登">
      <el-table-column label="提交人" width="150">
        <template #default="{ row }">
          {{ row.user?.name }}
          <div class="dim">{{ row.user?.student_id }}</div>
        </template>
      </el-table-column>
      <el-table-column label="活动名称" min-width="200">
        <template #default="{ row }"><b>{{ row.title }}</b></template>
      </el-table-column>
      <el-table-column label="活动日期" width="120" prop="service_date" />
      <el-table-column label="申报时长" width="100">
        <template #default="{ row }">{{ row.hours }}h</template>
      </el-table-column>
      <el-table-column label="提交时间" width="180">
        <template #default="{ row }">{{ fmtDT(row.submitted_at) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="150">
        <template #default="{ row }">
          <el-tag size="small" :type="STATUS_TYPES[row.status]">{{ row.status_label }}</el-tag>
          <span v-if="row.status === 'approved'" class="dim" style="margin-left:8px">
            计 {{ row.counted_hours }}h
          </span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button size="small" type="primary" link @click="openDrawer(row)">
            查看
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <Pager v-if="total > size" :page="page" :page-size="size" :total="total"
           @change="goPage" />

    <el-drawer v-model="drawer" title="校外时长补登" :size="narrow ? '100%' : '520px'">
      <template v-if="cur">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="提交人">
            {{ cur.user?.name }}（{{ cur.user?.student_id }}）
          </el-descriptions-item>
          <el-descriptions-item label="学院 / 校区">
            {{ cur.user?.college || '—' }} · {{ cur.user?.campus || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="手机号">{{ cur.user?.phone || '—' }}</el-descriptions-item>
          <el-descriptions-item label="活动名称">{{ cur.title }}</el-descriptions-item>
          <el-descriptions-item label="活动日期">{{ cur.service_date }}</el-descriptions-item>
          <el-descriptions-item label="申报时长">{{ cur.hours }} 小时</el-descriptions-item>
          <el-descriptions-item label="服务描述">
            <span class="pre">{{ cur.service_desc }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="备注">{{ cur.note || '—' }}</el-descriptions-item>
          <el-descriptions-item label="提交时间">{{ fmtDT(cur.submitted_at) }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag size="small" :type="STATUS_TYPES[cur.status]">{{ cur.status_label }}</el-tag>
            <span v-if="cur.status === 'approved'" class="dim" style="margin-left:8px">
              核定 {{ cur.counted_hours }} 小时
            </span>
          </el-descriptions-item>
          <el-descriptions-item v-if="cur.reply" label="意见">{{ cur.reply }}</el-descriptions-item>
        </el-descriptions>

        <div class="section-title">证明材料（{{ cur.files.proofs.length }}）</div>
        <FeedbackShots v-if="proofImages.length" :images="proofImages" />
        <div v-for="f in proofPdfs" :key="f.id" style="margin-top:8px">
          <el-button size="small" plain @click="download(f.url)">{{ f.title }}</el-button>
        </div>

        <div class="section-title">活动照片（{{ cur.files.photos.length }}）</div>
        <FeedbackShots :images="cur.files.photos" />

        <div v-if="cur.status === 'pending' && cur.mine" class="dim" style="margin-top:12px">
          这是您本人提交的补登，要由{{ vbDept }}的其他人审批。
        </div>
        <template v-else-if="cur.status === 'pending'">
          <div class="section-title">审批</div>
          <el-form label-width="72px">
            <el-form-item label="核定">
              <el-input-number v-model="decide.hours" :min="0.5" :max="cur.hours" :step="0.5" />
              <span class="dim" style="margin-left:12px">
                小时，只能核减（不高于申报的 {{ cur.hours }}），要加请驳回让他改后重提
              </span>
            </el-form-item>
            <el-form-item label="意见">
              <el-input v-model="decide.reply" type="textarea" :rows="3" maxlength="500"
                        show-word-limit placeholder="驳回必填；通过时选填" />
            </el-form-item>
          </el-form>
          <div class="foot">
            <el-button type="danger" plain :loading="decide.busy" @click="submit(false)">
              驳回
            </el-button>
            <el-button type="success" :loading="decide.busy" @click="submit(true)">
              通过
            </el-button>
          </div>
        </template>
        <div v-else-if="cur.status === 'approved' && cur.mine" class="dim"
             style="margin-top:12px">
          这是您本人提交的补登，要撤销请找{{ vbDept }}的其他人。
        </div>
        <div v-else-if="cur.status === 'approved'" class="foot">
          <el-button type="danger" plain @click="revoke">撤销这条补登</el-button>
        </div>
      </template>
    </el-drawer>
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, download, fmtDT } from '../api'
import { auth } from '../store'
import { useNarrow } from '../composables/useNarrow'
import Pager from './Pager.vue'
import FeedbackShots from './FeedbackShots.vue'

const STATUS_TYPES = { pending: 'warning', approved: 'success',
                       rejected: 'danger', revoked: 'info' }
const narrow = useNarrow()
// 志愿北京对接部门的实际名称（登录信息里带下来），文案里别写死部门名
const vbDept = computed(() => auth.user?.vb_dept_name || '校青协')
const status = ref('pending')
const items = ref([])
const total = ref(0)
const pending = ref(0)
const page = ref(1)
const size = 20
const loading = ref(false)
const canDecide = ref(false)
const drawer = ref(false)
const cur = ref(null)
const decide = ref({ hours: 1, reply: '', busy: false })

const proofImages = computed(
  () => (cur.value?.files.proofs || []).filter(f => !isPdf(f)))
const proofPdfs = computed(
  () => (cur.value?.files.proofs || []).filter(f => isPdf(f)))
const isPdf = f => (f.title || '').toLowerCase().endsWith('.pdf')

async function load() {
  loading.value = true
  try {
    const q = new URLSearchParams({ status: status.value, page: page.value,
                                    size: String(size) })
    const d = await api.get(`/api/staff/hours-claims?${q}`)
    canDecide.value = d.can_decide
    items.value = d.items
    total.value = d.total
    pending.value = d.pending
  } finally { loading.value = false }
}
onMounted(load)

function goPage(p) {
  page.value = p
  load()
}

// 换筛选条件回第 1 页：停在第 3 页多半是空的，看着像「没有这一类补登」
function reload() {
  page.value = 1
  load()
}

function openDrawer(row) {
  cur.value = row
  decide.value = { hours: row.hours, reply: '', busy: false }
  drawer.value = true
}

async function submit(approve) {
  const d = decide.value
  if (!approve && d.reply.trim().length < 2) {
    return ElMessage.warning('驳回要写明理由，本人照着补材料或核对')
  }
  d.busy = true
  try {
    await api.post(`/api/staff/hours-claims/${cur.value.id}/decide`,
                   { approve, hours: approve ? d.hours : null, reply: d.reply })
    ElMessage.success(approve ? '已通过，时长已计入' : '已驳回，本人会收到通知')
    drawer.value = false
    load()
  } finally { d.busy = false }
}

async function revoke() {
  let value
  try {
    ({ value } = await ElMessageBox.prompt(
      '写清为什么撤销，这句话会发给本人、也进审计。',
      `撤销「${cur.value.title}」`,
      { inputType: 'textarea', inputPlaceholder: '如：材料与活动时间不符，本人已确认不再计',
        inputValidator: v => (v || '').trim().length >= 2 || '至少 2 个字' }))
  } catch { return }
  await api.post(`/api/staff/hours-claims/${cur.value.id}/revoke`,
                 { reason: value.trim() })
  ElMessage.success('已撤销，本人会收到通知')
  drawer.value = false
  load()
}

function exportAll() {
  download('/api/staff/hours-claims.xlsx')
}
</script>

<style scoped>
.pager { margin-top: 12px; justify-content: flex-end; }
.foot { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
</style>
