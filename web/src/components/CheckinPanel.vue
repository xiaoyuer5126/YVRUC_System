<template>
  <div v-if="boardShift">
    <!-- 本页自己管场次，不跟着「报名与调配」走：那边在排人，这边在盯现场，
         两件事同时在做，共用一个选择等于互相打架 -->
    <div class="toolbar">
      <span>签到场次</span>
      <el-select v-model="boardShiftId" style="width:280px" @change="onSwitchBoard">
        <el-option v-for="g in shiftOptions" :key="g.id" :value="g.id"
                   :label="`${g.name}（${g.time_text}）`">
          <span>{{ g.name }}</span>
          <el-tag v-if="g.kind !== 'service'" size="small" effect="plain"
                  style="margin-left:6px">{{ g.kind_label }}</el-tag>
          <span class="dim" style="margin-left:8px">{{ g.time_text }}</span>
        </el-option>
      </el-select>
      <!-- 刷新条放在这里而不是看板里：换场次时看板会整块卸载重建，
           待在里面的话组件跟着重来一次，「更新于」被清空 -->
      <RefreshBar ref="boardRefresh" :load="loadBoard" />
    </div>

    <div class="toolbar">
      <el-button type="primary" size="small" :disabled="over" @click="openCreate">
        <el-icon><Plus /></el-icon>&nbsp;生成签到码
      </el-button>
      <el-button size="small" :disabled="over" @click="openReminder">
        <el-icon><Bell /></el-icon>&nbsp;补发签到提醒
      </el-button>
      <span class="dim">
        {{ hoursFrozen ? '时长已封账，签到记录不再改动'
           : over ? '活动已结束，不再发码；漏签的人仍可在下方手动补签'
           : '同一个码：首次扫码签到，再次扫码签退' }}
      </span>
    </div>

    <!-- 场次与岗位放到 #body 里一起讲（要带培训/彩排标记），不再单列字段 -->
    <DataCards v-if="narrow" :rows="tasks" title-key="token" style="margin-bottom:16px"
               :fields="[]" empty="还没有签到码">
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
          <b>{{ row.shift_name }}</b>
          <template v-if="row.shift_kind !== 'service'">（{{ row.shift_kind_label }}）</template>
          <template v-if="row.shift_id === boardShiftId"> · 当前大屏</template><br />
          {{ row.mode === 'position' ? `分岗位·${row.position_name}` : '统一签到' }} ·
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
      <!-- 码表列的是整个活动的码：没有场次列就分不出哪张码是哪一场的 -->
      <el-table-column label="场次" min-width="150">
        <template #default="{row}">
          {{ row.shift_name }}
          <el-tag v-if="row.shift_kind !== 'service'" size="small" effect="plain">
            {{ row.shift_kind_label }}
          </el-tag>
          <el-tag v-if="row.shift_id === boardShiftId" size="small" type="success"
                  effect="plain">当前大屏</el-tag>
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
                         :disabled="over" @confirm="toggle(row)">
            <template #reference>
              <el-button size="small" :type="row.active ? 'danger' : 'success'" link
                         :disabled="over">
                {{ row.active ? '关闭' : '开启' }}
              </el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>

    <!-- 到岗看板：按场次拉取，不依赖激活任务 -->
    <template v-if="board">
      <div class="section-title">{{ board.shift_name }} · 到岗情况
        <el-tag v-if="board.kind !== 'service'" size="small" effect="plain">
          {{ board.kind_label }}
        </el-tag>
      </div>
      <el-row :gutter="14">
        <el-col :span="6">
          <el-card shadow="never" class="big-card">
            <div class="ratio"><b>{{ board.checked }}</b> / {{ board.expected }}</div>
            <div class="dim">已到岗 / 应到岗</div>
            <div class="dim" style="margin-top:4px">已签退 {{ board.checked_out }} 人</div>
            <div v-if="board.excused" class="dim">已请假 {{ board.excused }} 人（不计入应到岗）</div>
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

      <div class="section-title">到场名单（含未签到者，勾选后可批量操作）</div>
      <el-card shadow="never">
        <div class="manual-bar">
          <el-input v-model="manualSid" size="small" placeholder="输入学号单个操作" style="width:180px"
                    :disabled="!batchTaskId || hoursFrozen" />
          <el-button size="small" :disabled="!batchTaskId || hoursFrozen" @click="quick('checkin')">签到</el-button>
          <el-button size="small" :disabled="!batchTaskId || hoursFrozen" @click="quick('checkout')">签退</el-button>
          <el-divider direction="vertical" />
          <template v-if="selected.length">
            <span class="dim">已选 {{ selected.length }} 人：</span>
            <el-button size="small" type="primary" :disabled="!batchTaskId || hoursFrozen"
                       @click="batch('checkin')">批量签到</el-button>
            <el-button size="small" type="warning" :disabled="!batchTaskId || hoursFrozen"
                       @click="batch('checkout')">批量签退</el-button>
            <el-button size="small" :disabled="!batchTaskId || hoursFrozen"
                       @click="batch('cancel_checkout')">取消签退</el-button>
            <el-button size="small" type="danger" plain :disabled="!batchTaskId || hoursFrozen"
                       @click="batch('cancel_checkin')">取消签到</el-button>
            <el-tag v-if="!batchTaskId" size="small" type="warning" effect="plain">请先生成并开启签到码</el-tag>
          </template>
          <template v-else>
            <el-tag v-if="!batchTaskId" size="small" type="warning" effect="plain">请先生成并开启签到码</el-tag>
          </template>
        </div>
        <DataCards v-if="narrow" :rows="board.records" title-key="name"
               row-key="student_id" :selectable="!!batchTaskId && !hoursFrozen"
               @selection-change="onSelect"
               :fields="[{ key: 'student_id', label: '学号' },
                         { key: 'position', label: '岗位' },
                         { key: 'time', label: '签到', hideEmpty: true },
                         { key: 'checkout_at', label: '签退', hideEmpty: true }]"
               empty="名单为空">
      <template #extra="{ row }">
        <el-tag v-if="row.is_leader" size="small" type="warning" effect="dark">组长</el-tag>
        <el-tag size="small"
                :type="row.checked_in ? 'success' : (row.excused ? 'info' : 'danger')"
                :effect="row.checked_in ? 'light' : 'plain'">
          {{ row.checked_in ? (row.checkout_at ? '已签退' : '已到岗')
             : (row.excused ? '已请假' : '未签到') }}
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
              <el-tag v-if="row.excused && !row.checked_in" size="small"
                      type="info" effect="plain">已请假</el-tag>
              <el-tag v-else-if="!row.checked_in" size="small" type="info" effect="plain">未签到</el-tag>
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
    <el-dialog v-model="creating" title="生成签到码" width="460">
      <el-form label-width="110px">
        <!-- 一个码只属于一个场次，所以场次必须在这里选定，不能跟着别的页签走 -->
        <el-form-item label="签到场次" required>
          <el-select v-model="form.shift_id" style="width:100%" @change="onPickShift">
            <el-option v-for="g in shiftOptions" :key="g.id" :value="g.id"
                       :label="`${g.name}（${g.time_text}）`">
              <span>{{ g.name }}</span>
              <el-tag v-if="g.kind !== 'service'" size="small" effect="plain"
                      style="margin-left:6px">{{ g.kind_label }}</el-tag>
              <span class="dim" style="margin-left:8px">{{ g.time_text }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="签到模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="shift">统一签到</el-radio>
            <el-radio value="position" :disabled="!formShift?.positions?.length">
              分岗位签到
            </el-radio>
          </el-radio-group>
          <div v-if="!formShift?.positions?.length" class="dim">
            {{ formShift?.kind !== 'service'
               ? `${formShift?.kind_label}不单设岗位，只能统一签到`
               : '本场次还没有岗位，只能统一签到' }}
          </div>
        </el-form-item>
        <el-form-item v-if="form.mode === 'position'" label="选择岗位" required>
          <el-select v-model="form.position_id" style="width:100%">
            <el-option v-for="s in formShift?.positions || []" :key="s.id" :value="s.id"
                       :label="`${s.name}（${s.time_text}）`" />
          </el-select>
        </el-form-item>
        <el-form-item label="可签时段">
          <div class="two-col">
            <DateTimeField :model-value="form.range[0]" placeholder="开放签到"
                           @update:model-value="setWindowStart" />
            <DateTimeField :model-value="form.range[1]" placeholder="停止签到"
                           @update:model-value="setWindowEnd" />
          </div>
          <div class="dim">默认比场次起止各放宽 {{ WINDOW_PAD }} 分钟，可自行调整。</div>
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
        <!-- 必须带 base：不传的话码里编的是相对路径，相机只会当一行字显示 -->
        <img :src="`/api/checkin/qr.svg?token=${qrTask.token}&base=${qrBase()}&t=${Date.now()}`"
             width="280" height="280" alt="签到二维码" />
        <div class="mono" style="font-size:22px;letter-spacing:4px;margin-top:6px">{{ qrTask.token }}</div>
        <!-- 这张码是哪一场、哪个岗位的：打印出来贴到现场，光看六位码分不出 -->
        <div class="qr-who">
          <b>{{ qrTask.shift_name }}</b>
          <el-tag v-if="qrTask.shift_kind !== 'service'" size="small" effect="plain">
            {{ qrTask.shift_kind_label }}
          </el-tag>
          <template v-if="qrTask.mode === 'position'">
            <span class="sep">·</span><b>{{ qrTask.position_name }}</b>
            <el-tag size="small" effect="plain">分岗位</el-tag>
          </template>
          <el-tag v-else size="small" effect="plain">统一签到</el-tag>
        </div>
        <div class="dim">可签 {{ fmtDT(qrTask.valid_from) }} ~ {{ fmtDT(qrTask.valid_to) }}</div>
        <div class="dim">首次扫码签到，再次扫码签退</div>
        <div style="margin-top:10px">
          <el-input size="small" readonly :model-value="scanUrl">
            <template #append><el-button @click="copyLink">复制</el-button></template>
          </el-input>
        </div>
      </div>
    </el-dialog>
  </div>

    <!-- 补发签到提醒：先选范围再发，别在下拉里盲点 -->
    <el-dialog v-model="reminderDlg" title="补发签到提醒" width="460">
      <p class="dim" style="margin-top:0;line-height:1.8">
        只发给<b>未签到</b>的正式志愿者。场次开始前 30 分钟系统已自动发送过一次。
      </p>
      <el-form label-width="72px">
        <el-form-item label="范围">
          <el-radio-group v-model="reminderScope">
            <el-radio value="all">全场次</el-radio>
            <el-radio v-if="positionGroups.length" value="group">按岗位组</el-radio>
            <el-radio value="position">按岗位</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="reminderScope === 'group'" label="岗位组" required>
          <el-select v-model="reminderGroup" placeholder="选择岗位组" style="width:100%">
            <el-option v-for="gn in positionGroups" :key="gn" :value="gn" :label="gn" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="reminderScope === 'position'" label="岗位" required>
          <el-select v-model="reminderPositions" multiple placeholder="可多选"
                     style="width:100%">
            <el-option v-for="p in boardShift?.positions || []" :key="p.id" :value="p.id"
                       :label="p.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="签收">
          <el-checkbox v-model="reminderAck">要求志愿者点「我已知悉」</el-checkbox>
          <div class="dim">勾选后可在「签收情况」查看并一键提醒。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reminderDlg = false">取消</el-button>
        <el-button type="primary" :loading="reminding" @click="sendReminder">
          发送
        </el-button>
      </template>
    </el-dialog>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, fmtDT, pageUrl, qrBase } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from './DataCards.vue'
import DateTimeField from './DateTimeField.vue'
import RefreshBar from './RefreshBar.vue'

const props = defineProps({
  activityId: { type: String, required: true },
  allShifts: { type: Array, required: true },  // 主场次 + 培训/彩排
  // 活动已结束：不再发新的签到码，也不催人签到——没人再扫了
  over: { type: Boolean, default: false },
  // 时长已封账（锁定或正在审核）：连手动补签也停，签到记录是算时长的依据
  hoursFrozen: { type: Boolean, default: false },
})

// 与后端 config.CHECKIN_WINDOW_PAD_MINUTES 保持一致
const WINDOW_PAD = 30
const route = useRoute()
const actId = computed(() => props.activityId)   // vuecheck: script-only

const creating = ref(false)
const form = ref({ shift_id: '', mode: 'shift', position_id: '', range: [] })

const shiftOptions = computed(() => props.allShifts)
const formShift = computed(
  () => shiftOptions.value.find(g => g.id === form.value.shift_id))

// 本页自己持有的场次选择，与「报名与调配」互不影响
const boardShiftId = ref('')
const boardShift = computed(
  () => shiftOptions.value.find(g => g.id === boardShiftId.value))

const narrow = useNarrow()
const tasks = ref([])
const board = ref(null)
const qrVisible = ref(false)
const qrTask = ref(null)
const manualSid = ref('')
const selected = ref([])
const boardRefresh = ref(null)

const pct = computed(() => board.value
  ? Math.min(100, Math.round(board.value.checked / Math.max(1, board.value.expected) * 100))
  : 0)

// 扫码链接放 computed：模板里不能直接用 location（不在 Vue 模板全局白名单）
const scanUrl = computed(() => pageUrl(`/checkin/${qrTask.value?.token || ''}`))

// 批量/单个补签需要一个 task_id，且它必须属于**当前大屏这一场**：码表列的是
// 整个活动的码，取「第一个激活的」可能取到别的场次，后端按 task.shift_id 找人，
// 结果整屏人被判成「非本场次确认志愿者」全部跳过。后端列表按创建时间倒序，
// 故本场次第一个激活的即最新。
const batchTaskId = computed(() => tasks.value.find(
  t => t.active && t.shift_id === boardShiftId.value)?.id || '')

async function loadTasks() {
  tasks.value = await api.get(`/api/staff/activities/${actId.value}/checkin-tasks`)
}

/** 到岗看板 + 码列表一起拉：现场补签会同时改这两处的数字。 */
async function loadBoard() {
  if (!boardShiftId.value) return
  const [b] = await Promise.all([
    api.get(`/api/staff/shifts/${boardShiftId.value}/board`),
    loadTasks(),
  ])
  board.value = b
}

/** 换场次：看板整份换掉，先前勾选的人不再属于这一场，必须清空。 */
function onSwitchBoard() {
  board.value = null
  selected.value = []
  manualSid.value = ''
  boardRefresh.value?.fire()
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

onMounted(async () => {
  // 深链 ?qr=GALA01 直接打开二维码弹窗，并把看板切到那张码所在的场次
  const qrt = route.query.qr
  await loadTasks()
  const row = qrt && tasks.value.find(t => t.token === String(qrt).toUpperCase())
  boardShiftId.value = row?.shift_id || shiftOptions.value[0]?.id || ''
  if (row) { qrTask.value = row; qrVisible.value = true }
  await loadBoard()
  boardRefresh.value?.markLoaded()
})

/** 默认可签时段：场次起止各外扩 WINDOW_PAD 分钟。
 *
 * 卡着场次时间开闸的话，提前到岗的人签不了、拖堂散场的人也签不了。
 */
function defaultRange(g) {
  const pad = WINDOW_PAD * 60 * 1000
  const s = new Date((g?.start_time || '').replace(' ', 'T'))
  const e = new Date((g?.end_time || '').replace(' ', 'T'))
  if (Number.isNaN(s.getTime()) || Number.isNaN(e.getTime())) return []
  return [new Date(s.getTime() - pad), new Date(e.getTime() + pad)]
}

function openCreate() {
  const g = boardShift.value || shiftOptions.value[0]
  form.value = { shift_id: g?.id || '', mode: 'shift', position_id: '',
                 range: defaultRange(g) }
  creating.value = true
}

/** 换了场次：时段跟着换，岗位清空——旧岗位不属于新场次，后端会 400。 */
function onPickShift() {
  const g = formShift.value
  form.value.range = defaultRange(g)
  form.value.position_id = ''
  if (!g?.positions?.length) form.value.mode = 'shift'
}

async function createTask() {
  if (!form.value.shift_id) return ElMessage.warning('请选择签到场次')
  if (form.value.mode === 'position' && !form.value.position_id)
    return ElMessage.warning('请选择岗位')
  const body = {
    mode: form.value.mode,
    position_id: form.value.mode === 'position' ? form.value.position_id : null,
  }
  const [f1, f2] = form.value.range || []
  if (f1) body.valid_from = toISO(f1)
  if (f2) body.valid_to = toISO(f2)
  await api.post(`/api/staff/shifts/${form.value.shift_id}/checkin-tasks`, body)
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
  (boardShift.value?.positions || []).map(p => p.group_name).filter(Boolean))])

const reminderDlg = ref(false)
const reminderScope = ref('all')
const reminderGroup = ref('')
const reminderPositions = ref([])
const reminderAck = ref(false)

function openReminder() {
  reminderScope.value = 'all'
  reminderGroup.value = ''
  reminderPositions.value = []
  reminderAck.value = false
  reminderDlg.value = true
}

async function sendReminder() {
  const body = { need_ack: reminderAck.value }
  if (reminderScope.value === 'group') {
    if (!reminderGroup.value) return ElMessage.warning('请选择岗位组')
    body.group_name = reminderGroup.value
  } else if (reminderScope.value === 'position') {
    if (!reminderPositions.value.length) return ElMessage.warning('请选择岗位')
    body.position_ids = reminderPositions.value
  }
  reminding.value = true
  try {
    const d = await api.post(
      `/api/staff/shifts/${boardShiftId.value}/checkin-reminder`, body)
    ElMessage.success(`已向${d.scope}的 ${d.count} 位未签到志愿者发送提醒`
      + (d.batch_id ? '（要求签收）' : ''))
    reminderDlg.value = false
  } finally { reminding.value = false }
}

async function toggle(row) {
  await api.patch(`/api/staff/checkin-tasks/${row.id}`, { active: !row.active })
  loadTasks(); loadBoard()
}

function setWindowStart(v) { form.value.range = [v || '', form.value.range[1] || ''] }
function setWindowEnd(v) { form.value.range = [form.value.range[0] || '', v || ''] }
</script>

<style scoped>
/* 起止两个选择器并排；窄屏自动叠成两行 */
.two-col { display: flex; gap: 8px; width: 100%; flex-wrap: wrap; }
.two-col > * { flex: 1 1 180px; min-width: 0; }
.toolbar { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
.big-card { text-align:center; border-radius:12px; }
.ratio b { font-size:30px; color: var(--ruc-red); }
.st-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(150px,1fr)); gap:12px; }
.manual-bar { display:flex; gap:8px; margin-bottom:8px; align-items:center; flex-wrap:wrap; }
/* 二维码下面那行「这是哪一场、哪个岗位」 */
.qr-who { display:flex; justify-content:center; align-items:center; gap:6px;
          flex-wrap:wrap; margin:6px 0 4px; font-size:14px; }
.qr-who .sep { color:#c0c4cc; }
:deep(tr.row-wait td.el-table__cell) { color:#9c9c9c; }
</style>
