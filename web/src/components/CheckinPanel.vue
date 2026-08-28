<template>
  <div v-if="shift">
    <div class="toolbar">
      <el-button type="primary" size="small" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;生成签到码
      </el-button>
      <el-dropdown split-button size="small" :loading="reminding"
                   @click="sendReminder()" @command="sendReminder">
        <el-icon><Bell /></el-icon>&nbsp;补发签到提醒
        <template #dropdown>
          <el-dropdown-menu>
            <div class="dim" style="padding:4px 14px">只发给还没签到的人</div>
            <el-dropdown-item :command="undefined">全场次</el-dropdown-item>
            <el-dropdown-item v-for="gn in positionGroups" :key="'g:' + gn"
                              :command="{ group_name: gn }" divided>
              岗位组「{{ gn }}」
            </el-dropdown-item>
            <el-dropdown-item v-for="p in shift.positions" :key="p.id"
                              :command="{ position_ids: [p.id] }">
              岗位「{{ p.name }}」
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <span class="dim">同一签到码：第一次扫=签到，第二次扫=签退；场次结束2小时后未签退，自动按场次结束时间计</span>
    </div>

    <DataCards v-if="narrow" :rows="tasks" title-key="token" style="margin-bottom:16px"
               :fields="[{ key: 'shift_name', label: '场次' },
                         { key: 'position_name', label: '岗位', hideEmpty: true }]"
               empty="还没有签到码">
      <template #title="{ row }">
        <span class="mono" style="font-size:18px;letter-spacing:3px">{{ row.token }}</span>
      </template>
      <template #extra="{ row }">
        <el-tag size="small" :type="row.active ? 'success' : 'info'">
          {{ row.active ? '开启' : '已关闭' }}
        </el-tag>
      </template>
      <template #body="{ row }">
        <div class="dim">
          {{ row.mode === 'position' ? '分岗位签到' : '统一签到' }} ·
          {{ fmtDT(row.valid_from) }} ~ {{ fmtDT(row.valid_to) }} · 已签 {{ row.count }} 人
        </div>
      </template>
      <template #actions="{ row }">
        <el-button size="small" @click="openQR(row)">二维码 / 大屏</el-button>
        <el-button size="small" :type="row.active ? 'danger' : 'success'" link
                   @click="toggle(row)">{{ row.active ? '关闭' : '开启' }}</el-button>
      </template>
    </DataCards>
    <el-table v-else :data="tasks" size="small" style="margin-bottom:16px">
      <el-table-column label="签到码" width="110">
        <template #default="{row}">
          <b class="mono" style="font-size:15px;letter-spacing:2px">{{ row.token }}</b>
        </template>
      </el-table-column>
      <el-table-column label="模式" width="130">
        <template #default="{row}">
          {{ row.mode === 'position' ? `岗位·${row.position_name}` : '统一签到' }}
        </template>
      </el-table-column>
      <el-table-column label="有效时段" min-width="180">
        <template #default="{row}">{{ fmtDT(row.valid_from) }} ~ {{ fmtDT(row.valid_to) }}</template>
      </el-table-column>
      <el-table-column label="已签/应签" width="100">
        <template #default="{row}">{{ row.count }} 人</template>
      </el-table-column>
      <el-table-column label="操作" width="220">
        <template #default="{row}">
          <el-button size="small" @click="openQR(row)">二维码 / 大屏</el-button>
          <el-popconfirm :title="row.active ? '关闭后无法继续扫码' : '重新开启？'"
                         @confirm="toggle(row)">
            <template #reference>
              <el-button size="small" :type="row.active ? 'danger' : 'success'" link>
                {{ row.active ? '关闭' : '开启' }}
              </el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <!-- 实时大屏：按场次拉取，不依赖激活任务 -->
    <template v-if="board">
      <div class="section-title">{{ board.shift_name }} · 实时到岗
        <el-tag size="small" effect="plain">每5秒刷新</el-tag>
      </div>
      <el-row :gutter="14">
        <el-col :span="6">
          <el-card shadow="never" class="big-card">
            <div class="ratio"><b>{{ board.checked }}</b> / {{ board.expected }}</div>
            <div class="dim">已到岗 / 应到岗</div>
            <div class="dim" style="margin-top:4px">已签退 {{ board.checked_out }} 人</div>
            <el-progress type="circle" :percentage="pct" :width="86"
                         :color="pct === 100 ? '#67c23a' : '#9e2235'" style="margin-top:8px" />
          </el-card>
        </el-col>
        <el-col :span="18">
          <el-card shadow="never">
            <div class="st-grid">
              <div v-for="s in board.positions" :key="s.id" class="st-box">
                <b>{{ s.name }}</b>
                <div class="dim">{{ s.checked }}/{{ s.quota }} 到岗</div>
                <el-progress :percentage="Math.min(100, Math.round(s.checked / Math.max(1, s.quota) * 100))"
                             :stroke-width="7" :show-text="false"
                             :color="s.checked >= s.quota ? '#67c23a' : '#9e2235'" />
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <div class="section-title">到场名单（含未签到者，勾选后可直接批量签到）</div>
      <el-card shadow="never">
        <div class="manual-bar">
          <el-input v-model="manualSid" size="small" placeholder="输入学号单个操作" style="width:180px"
                    :disabled="!batchTaskId" />
          <el-button size="small" :disabled="!batchTaskId" @click="quick('checkin')">签到</el-button>
          <el-button size="small" :disabled="!batchTaskId" @click="quick('checkout')">签退</el-button>
          <el-divider direction="vertical" />
          <template v-if="selected.length">
            <span class="dim">已选 {{ selected.length }} 人：</span>
            <el-button size="small" type="primary" :disabled="!batchTaskId"
                       @click="batch('checkin')">批量签到</el-button>
            <el-button size="small" type="warning" :disabled="!batchTaskId"
                       @click="batch('checkout')">批量签退</el-button>
            <el-button size="small" :disabled="!batchTaskId"
                       @click="batch('cancel_checkout')">取消签退</el-button>
            <el-button size="small" type="danger" plain :disabled="!batchTaskId"
                       @click="batch('cancel_checkin')">取消签到</el-button>
            <el-tag v-if="!batchTaskId" size="small" type="warning" effect="plain">请先生成并开启签到码</el-tag>
          </template>
          <template v-else>
            <span v-if="batchTaskId" class="dim">勾选左侧复选框可批量签到/签退/取消（未签到者也可直接补签）</span>
            <el-tag v-else size="small" type="warning" effect="plain">请先生成并开启签到码</el-tag>
          </template>
        </div>
        <DataCards v-if="narrow" :rows="board.records" title-key="name"
               row-key="student_id"
               :fields="[{ key: 'student_id', label: '学号' },
                         { key: 'position', label: '岗位' },
                         { key: 'time', label: '签到', hideEmpty: true },
                         { key: 'checkout_at', label: '签退', hideEmpty: true }]"
               empty="名单为空">
      <template #extra="{ row }">
        <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark">组长</el-tag>
        <el-tag size="small" :type="row.checked_in ? 'success' : 'info'"
                :effect="row.checked_in ? 'light' : 'plain'">
          {{ row.checked_in ? (row.checkout_at ? '已签退' : '已到岗') : '未签到' }}
        </el-tag>
      </template>
    </DataCards>
    <el-table v-else :data="board.records" size="small" max-height="320"
                  :row-class-name="rowClass" @selection-change="onSelect">
          <el-table-column type="selection" width="40" />
          <el-table-column label="签到" width="90">
            <template #default="{row}">{{ row.time || '—' }}</template>
          </el-table-column>
          <el-table-column label="签退" width="90">
            <template #default="{row}">
              {{ row.checkout_at || '—' }}
            </template>
          </el-table-column>
          <el-table-column label="姓名" width="120">
            <template #default="{row}">
              {{ row.name }}
              <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark"
                      style="margin-left:4px">组长</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="student_id" label="学号" width="120" />
          <el-table-column prop="college" label="学院" min-width="140" />
          <el-table-column prop="position" label="岗位" min-width="120" />
          <el-table-column label="方式" width="130">
            <template #default="{row}">
              <el-tag v-if="!row.checked_in" size="small" type="info" effect="plain">未签到</el-tag>
              <template v-else>
                <el-tag size="small" :effect="row.method === 'scan' ? 'light' : 'plain'">
                  {{ row.method === 'scan' ? '扫码' : '补签' }}
                </el-tag>
                <el-tag v-if="row.checkout_method === 'manual'" size="small" effect="plain"
                        style="margin-left:4px">手动退</el-tag>
              </template>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <!-- 生成签到码 -->
    <el-dialog v-model="creating" title="生成签到码" width="440">
      <el-form label-width="110px">
        <el-form-item label="签到模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="shift">统一签到</el-radio>
            <el-radio value="position">分岗位签到</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.mode === 'position'" label="选择岗位" required>
          <el-select v-model="form.position_id" style="width:100%">
            <el-option v-for="s in shift.positions" :key="s.id" :value="s.id"
                       :label="`${s.name}（${s.time_text}）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="可签时段">
          <el-date-picker v-model="form.range" type="datetimerange" style="width:100%"
                          start-placeholder="开放签到" end-placeholder="停止签到" />
          <div class="dim">默认为场次起止时间，可自行调整：提前开放即允许志愿者提前到岗签到</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="creating = false">取消</el-button>
        <el-button type="primary" @click="createTask">生成</el-button>
      </template>
    </el-dialog>

    <!-- 二维码 -->
    <el-dialog v-model="qrVisible" title="现场签到二维码" width="380">
      <div style="text-align:center" v-if="qrTask">
        <img :src="`/api/checkin/qr.svg?token=${qrTask.token}&t=${Date.now()}`"
             width="280" height="280" alt="签到二维码" />
        <div class="mono" style="font-size:22px;letter-spacing:4px;margin-top:6px">{{ qrTask.token }}</div>
        <div class="dim">学生扫码进入签到页；第一次扫=签到，第二次扫=签退</div>
        <div style="margin-top:10px">
          <el-input size="small" readonly :model-value="scanUrl">
            <template #append><el-button @click="copyLink">复制</el-button></template>
          </el-input>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from './DataCards.vue'

const props = defineProps({
  shift: { type: Object, required: true },
  activityId: { type: String, default: '' },   // 兜底：序列化器缺 activity_id 时由父级传入
})
const route = useRoute()
const actId = computed(() => props.activityId || props.shift.activity_id)  // vuecheck: script-only

const creating = ref(false)
const form = ref({ mode: 'shift', position_id: '', range: [] })
const narrow = useNarrow()
const tasks = ref([])
const board = ref(null)
const qrVisible = ref(false)
const qrTask = ref(null)
const manualSid = ref('')
const selected = ref([])
let timer = null

const pct = computed(() => board.value
  ? Math.min(100, Math.round(board.value.checked / Math.max(1, board.value.expected) * 100))
  : 0)

// 扫码链接放 computed：模板里不能直接用 location（不在 Vue 模板全局白名单）
const scanUrl = computed(() =>
  `${window.location.origin}${window.location.pathname}#/checkin/${qrTask.value?.token || ''}`)

// 批量/单个签到签退仍需 task_id：取「最新生成的激活任务」。
// 后端列表按创建时间倒序返回，故第一个激活任务即最新。
const batchTaskId = computed(() => tasks.value.find(t => t.active)?.id || '')

async function loadTasks() {
  tasks.value = await api.get(`/api/staff/activities/${actId.value}/checkin-tasks`)
}

async function loadBoard() {
  try {
    board.value = await api.get(`/api/staff/shifts/${props.shift.id}/board`)
  } catch { /* toast by api；轮询失败保留旧数据 */ }
}

function onSelect(rows) { selected.value = rows }
function rowClass({ row }) { return row.checked_in ? '' : 'row-wait' }

async function batch(action) {
  if (!batchTaskId.value) return ElMessage.warning('请先生成并开启签到码')
  const sids = selected.value.map(r => r.student_id)
  if (!sids.length) return
  const d = await api.post(`/api/staff/checkin-tasks/${batchTaskId.value}/batch`,
    { action, student_ids: sids })
  ElMessage.success(`${d.done} 人操作成功` +
    (d.skipped.length ? `；跳过：${d.skipped.join('、')}` : ''))
  loadBoard()
}

async function quick(action) {
  if (!batchTaskId.value) return ElMessage.warning('请先生成并开启签到码')
  if (!manualSid.value.trim()) return ElMessage.warning('请输入学号')
  const d = await api.post(`/api/staff/checkin-tasks/${batchTaskId.value}/batch`,
    { action, student_ids: [manualSid.value.trim()] })
  if (d.done) {
    ElMessage.success(`${d.names.join('、')} ${action === 'checkin' ? '签到' : '签退'}成功`)
    manualSid.value = ''
  } else {
    ElMessage.warning(d.skipped.join('；'))
  }
  loadBoard()
}

onMounted(() => {
  loadTasks().then(() => {
    const qrt = route.query.qr   // 深链：?qr=GALA01 直接打开二维码弹窗
    if (qrt) {
      const row = tasks.value.find(t => t.token === String(qrt).toUpperCase())
      if (row) { qrTask.value = row; qrVisible.value = true }
    }
  })
  loadBoard()
  timer = setInterval(loadBoard, 5000)
})
onUnmounted(() => clearInterval(timer))

function openCreate() {
  // 默认可签时段 = 场次起止；管理者可自行提前开放签到
  const s = props.shift.start_time?.replace(' ', 'T') || ''
  const e = props.shift.end_time?.replace(' ', 'T') || ''
  form.value = { mode: 'shift', position_id: '',
                 range: [new Date(s), new Date(e)] }
  creating.value = true
}

async function createTask() {
  if (form.value.mode === 'position' && !form.value.position_id)
    return ElMessage.warning('请选择岗位')
  const body = {
    mode: form.value.mode,
    position_id: form.value.mode === 'position' ? form.value.position_id : null,
  }
  const [f1, f2] = form.value.range || []
  if (f1) body.valid_from = toISO(f1)
  if (f2) body.valid_to = toISO(f2)
  await api.post(`/api/staff/shifts/${props.shift.id}/checkin-tasks`, body)
  ElMessage.success('签到码已生成')
  creating.value = false
  await loadTasks()
}

function toISO(v) {
  if (!v) return null
  const d = v instanceof Date ? v : new Date(v)
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

function openQR(row) {
  qrTask.value = row
  qrVisible.value = true
}
function copyLink() {
  navigator.clipboard.writeText(scanUrl.value)
  ElMessage.success('已复制')
}
const reminding = ref(false)

// 现场催场往往是「东门那三个人没到」，所以可以只催某个岗位组或岗位
const positionGroups = computed(() => [...new Set(
  (props.shift.positions || []).map(p => p.group_name).filter(Boolean))])

async function sendReminder(scope) {
  reminding.value = true
  try {
    const d = await api.post(
      `/api/staff/shifts/${props.shift.id}/checkin-reminder`, scope || {})
    ElMessage.success(`已向 ${d.scope} 的 ${d.count} 位未签到志愿者发送提醒`)
  } finally { reminding.value = false }
}

async function toggle(row) {
  await api.patch(`/api/staff/checkin-tasks/${row.id}`, { active: !row.active })
  loadTasks(); loadBoard()
}
</script>

<style scoped>
.toolbar { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
.big-card { text-align:center; border-radius:12px; }
.ratio b { font-size:30px; color: var(--ruc-red); }
.st-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(150px,1fr)); gap:12px; }
.manual-bar { display:flex; gap:8px; margin-bottom:8px; align-items:center; flex-wrap:wrap; }
:deep(tr.row-wait td.el-table__cell) { color:#9c9c9c; }
</style>
