<!-- 报名后筛选：申请名单。看答案与档案，逐人或勾选几人录取 / 不录取。 -->
<template>
  <el-card shadow="never" v-loading="loading">
    <template #header>
      <div class="al-head">
        <b>申请名单</b>
        <span class="dim">{{ pendingCount }} 人待筛选 · 共 {{ apps.length }} 人</span>
        <el-button size="small" link type="primary" @click="load">刷新</el-button>
        <el-button size="small" link type="primary" @click="exportXlsx">导出 Excel</el-button>
        <!-- 默认停在「只看待处理」：进这个页面多半就是把这一批处理掉 -->
        <el-radio-group v-model="pendingOnly" size="small" @change="onFilterChange">
          <el-radio-button :value="true">只看待处理</el-radio-button>
          <el-radio-button :value="false">全部</el-radio-button>
        </el-radio-group>
        <span class="dim">导出仍是全部申请</span>
        <span class="al-actions">
          <span class="dim">{{ shiftsLoad }}</span>
          <el-button type="success" size="small" :disabled="!selected.length"
                     :loading="busy" @click="decide(true, selected)">录取所选</el-button>
          <el-button type="danger" size="small" plain :disabled="!selected.length"
                     :loading="busy" @click="decide(false, selected)">不录取所选</el-button>
        </span>
      </div>
    </template>
    <p class="dim" style="margin:0 0 8px">
      每行勾选录到他意向里的哪几场（默认全勾），选岗位的场次可以改岗位，再点这一行的「录取」；
      也可以勾选几个人后点上方「录取所选」，每人按自己那一行勾的场次录取。没勾的意向场次随即作废。
      名额不够、时间冲突只拦那一个人，其余照常处理，结果逐人列出；时间冲突的人可以确认后仍录取。
      统一分配的场次先落在「待分配」，再分配岗位；不录取只发送通知，不留记录。
      场次开始时仍未处理的申请会自动落选并通知志愿者。
    </p>
    <el-table ref="tableRef" :data="shown" size="small" row-key="id"
              :empty-text="pendingOnly ? '没有待处理的申请' : '还没有人申请'"
              @selection-change="onSelect">
      <el-table-column type="selection" width="36" :selectable="selectable" />
      <el-table-column type="expand" width="30">
        <template #default="{ row }">
          <AnswerList :answers="row.answers" style="margin:4px 0 4px 30px" />
        </template>
      </el-table-column>
      <!-- 先来后到：序号是第几个交的申请，提交时间紧跟在姓名后面，不用拖到最右边看 -->
      <el-table-column label="序号" prop="seq" width="56" />
      <!-- 状态紧挨着序号：列多了一屏放不下，处理没处理要能一眼看见 -->
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="STATUS_TYPE[row.status]">{{ row.status_label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="姓名" width="110">
        <template #default="{ row }">
          <ScoreAlert :user="row.user" />
          <el-link type="primary" underline="never" @click="$emit('archive', row.user)">
            {{ row.user.name }}
          </el-link>
          <!-- 学号跟在名字下面一行，省下的这一列宽度留给状态 -->
          <div class="dim">{{ row.user.student_id }}</div>
        </template>
      </el-table-column>
      <el-table-column label="提交时间" width="110">
        <template #default="{ row }">{{ fmtDT(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="学院" prop="user.college" min-width="120" />
      <el-table-column label="组长意愿" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.leader_willing" size="small" type="warning" effect="plain">愿意</el-tag>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
      <!-- 意向场次就是录取去向：还在申请中的意向逐场勾选（默认全勾），选岗位的
           场次后面跟岗位下拉，默认他自己勾的意向岗位；已处理的意向只显示结果 -->
      <el-table-column label="意向场次（勾选录取到哪几场）" min-width="300">
        <template #default="{ row }">
          <div v-for="w in row.wanted" :key="w.shift_id" class="al-pick">
            <template v-if="row.status === 'applied' && w.status === 'applied' && choice[row.id]">
              <el-checkbox v-model="choice[row.id][w.shift_id].on">{{ w.shift_name }}</el-checkbox>
              <el-select v-if="isPick(w.shift_id)" v-model="choice[row.id][w.shift_id].position_id"
                         size="small" style="width:160px" placeholder="岗位"
                         :disabled="!choice[row.id][w.shift_id].on">
                <el-option v-for="o in positionsOf(w.shift_id)" :key="o.id" :value="o.id"
                           :label="`${o.name}（${o.quota} 人）`" />
              </el-select>
              <span v-else class="dim">{{ w.position_name || '待分配' }}</span>
            </template>
            <el-tag v-else size="small" effect="plain"
                    :type="w.status === 'enrolled' ? 'success' : (w.status === 'applied' ? 'primary' : 'info')">
              {{ w.shift_name }}{{ w.position_name ? ' · ' + w.position_name : '' }}
            </el-tag>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130">
        <template #default="{ row }">
          <template v-if="row.status === 'applied'">
            <el-button size="small" type="success" link :disabled="busy"
                       @click="decide(true, [row])">录取</el-button>
            <el-button size="small" type="danger" link :disabled="busy"
                       @click="decide(false, [row])">不录取</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!apps.length && !loading" description="还没有人申请" />

    <!-- 逐人结果：成功的写录到哪儿，失败的写原因；时间冲突的可以确认后仍录取 -->
    <el-dialog v-model="result.visible" title="处理结果" width="600">
      <p class="dim" style="margin:0 0 8px">
        成功 {{ result.rows.filter(r => r.ok).length }} 人，未处理 {{ result.rows.filter(r => !r.ok).length }} 人
      </p>
      <div v-for="r in result.rows" :key="r.application_id" class="al-result">
        <el-tag size="small" :type="r.ok ? 'success' : (r.code === 'time_conflict' ? 'warning' : 'danger')">
          {{ r.ok ? '成功' : (r.code === 'time_conflict' ? '时间冲突' : '未处理') }}
        </el-tag>
        <div>
          <b>{{ r.name }}</b> <span class="dim">{{ r.student_id }}</span>
          <div v-if="r.ok && r.placed" class="dim">{{ r.placed }}</div>
          <div v-if="!r.ok">{{ r.reason }}</div>
          <div v-for="c in r.conflicts || []" :key="c.enrollment_id" class="dim">
            {{ conflictLine(c, activityId) }}
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="result.visible = false">关闭</el-button>
        <el-button v-if="clashRows.length" type="warning" :loading="busy" @click="confirmClashes">
          确认时间冲突，仍录取这 {{ clashRows.length }} 人
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, conflictLine, downloadExport, fmtDT } from '../api'
import ScoreAlert from './ScoreAlert.vue'
import AnswerList from './AnswerList.vue'

const props = defineProps({ activityId: { type: String, required: true } })
const emit = defineEmits(['archive', 'changed'])

const STATUS_TYPE = { applied: 'primary', accepted: 'success', rejected: 'info', withdrawn: 'info' }
const apps = ref([])
const shifts = ref([])   // vuecheck: script-only，模板经 positionsOf / isPick / shiftsLoad 读它
const selected = ref([])
// 每行勾了哪几场、各录到哪个岗位：申请 id → 场次 id → { on, position_id }。
// 载入时给每个待处理的人建好：还在申请中的意向全勾上，岗位取他自己勾的意向岗位；
// 刷新时已经改过的保留，录了一部分人之后剩下的人不用重勾
const choice = ref({})
const positionsOf = id => shifts.value.find(g => g.id === id)?.positions || []
const isPick = id => shifts.value.find(g => g.id === id)?.mode === 'pick'
  && positionsOf(id).length > 0
// 各场次已录/上限，放在上方按钮旁边，录人时心里有数
const shiftsLoad = computed(() => shifts.value
  .map(g => `${g.name} ${g.enrolled}/${g.capacity}`).join(' · '))
function initChoice(rows) {
  const out = {}
  for (const row of rows) {
    if (row.status !== 'applied') continue
    const before = choice.value[row.id] || {}
    out[row.id] = Object.fromEntries(row.wanted
      .filter(w => w.status === 'applied')
      .map(w => [w.shift_id, before[w.shift_id] || {
        on: true,
        position_id: positionsOf(w.shift_id).some(p => p.id === w.position_id)
          ? w.position_id : '',
      }]))
  }
  choice.value = out
}
/** 这一行勾的场次，按接口要的样子：[{ shift_id, position_id }] */
function picksOf(row) {
  return Object.entries(choice.value[row.id] || {})
    .filter(([, c]) => c.on)
    .map(([sid, c]) => ({ shift_id: sid, position_id: isPick(sid) ? c.position_id : '' }))
}
const loading = ref(false)
const busy = ref(false)
const pendingCount = computed(() => apps.value.filter(a => a.status === 'applied').length)
const pendingOnly = ref(true)
const shown = computed(() => (pendingOnly.value
  ? apps.value.filter(a => a.status === 'applied') : apps.value))
const tableRef = ref()
const selectable = row => row.status === 'applied'
const onSelect = rows => { selected.value = rows }
// 切开关要清勾选：行被筛掉以后，留着勾选会把看不见的人也一起提交
function onFilterChange() {
  tableRef.value?.clearSelection()
  selected.value = []
}

// 只下 Excel；有上传的文件时再问要不要一起下附件包
function exportXlsx() {
  downloadExport(`/api/staff/activities/${props.activityId}/applications`)
}

async function load() {
  loading.value = true
  try {
    const d = await api.get(`/api/staff/activities/${props.activityId}/applications`)
    shifts.value = d.shifts
    initChoice(d.applications)
    apps.value = d.applications
  } finally { loading.value = false }
}
onMounted(load)

// 逐人结果；clashRows 是因时间冲突没录上的那几个人。sent 记下提交时各人勾的
// 场次（[{ id, picks }]），确认冲突后按原样重提，不受名单刷新影响
const result = ref({ visible: false, rows: [], sent: [] })
const clashRows = computed(() => result.value.rows
  .filter(r => !r.ok && r.code === 'time_conflict'))

function post(items, accept, confirm) {
  return api.post(`/api/staff/activities/${props.activityId}/applications/decide`, {
    application_ids: items.map(a => a.id), accept, confirm_conflict: confirm,
    picks: accept ? Object.fromEntries(items.map(a => [a.id, a.picks])) : {},
  })
}

async function finish(rows, accept, d) {
  // 只处理一个人、又只是撞了时间：直接问一句，确认了就带 confirm_conflict 重提
  if (rows.length === 1 && accept && d.results.length === 1
      && d.results[0].code === 'time_conflict') {
    const r = d.results[0]
    try {
      await ElMessageBox.confirm(
        `${r.reason}。仍要录取 ${r.name}？`, '时间冲突',
        { type: 'warning', confirmButtonText: '仍要录取', cancelButtonText: '取消' })
    } catch { return }
    d = await post(rows, true, true)
  }
  if (rows.length === 1 && d.results.length === 1) {
    const r = d.results[0]
    if (r.ok) ElMessage.success(`${accept ? '已录取' : '已处理'} ${r.name}`)
    else ElMessage.error(`${r.name}：${r.reason}`)
  } else {
    result.value = { visible: true, rows: d.results, sent: rows }
  }
  if (d.count) {
    await load()
    emit('changed')
  }
}

async function decide(accept, list) {
  const rows = list.map(a => ({ id: a.id, name: a.user.name, picks: picksOf(a) }))
  if (accept) {
    const empty = rows.filter(a => !a.picks.length)
    if (empty.length) {
      ElMessage.warning(`${empty.map(a => a.name).join('、')} 没有勾选录取到哪一场`)
      return
    }
  }
  const names = rows.map(a => a.name).join('、')
  await ElMessageBox.confirm(
    accept ? `录取 ${names}？各人按自己那一行勾的场次录取，通知将立即发送。`
           : `不录取 ${names}？将发送通知，不留记录。`,
    accept ? '确认录取' : '确认不录取', { type: accept ? 'success' : 'warning' })
  busy.value = true
  try {
    await finish(rows, accept, await post(rows, accept, false))
  } finally { busy.value = false }
}

/** 结果框里时间冲突的那几个人：管理者看过冲突，确认后带 confirm_conflict 重提 */
async function confirmClashes() {
  const ids = new Set(clashRows.value.map(r => r.application_id))
  const rows = result.value.sent.filter(a => ids.has(a.id))
  busy.value = true
  try {
    const d = await post(rows, true, true)
    result.value = { visible: true, rows: d.results, sent: rows }
    if (d.count) {
      await load()
      emit('changed')
    }
  } finally { busy.value = false }
}
</script>

<style scoped>
.al-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.al-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.al-pick { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.al-result { display: flex; align-items: flex-start; gap: 8px; padding: 8px 0;
             border-bottom: 1px solid var(--el-border-color-lighter); }
</style>
