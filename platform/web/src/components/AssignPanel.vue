<template>
  <div v-loading="loading">
    <!-- 模式说明 -->
    <el-alert v-if="fineModeAllAssigned" type="info" :closable="false" style="margin-bottom:10px"
              title="精细化场次：志愿者报名时已各自选定工位"
              description="智能排班主要服务于「二级待定」场次。本场次运行试算一般会维持现状（结果中会标注「保持」）；如需个别调整，可直接修改下方建议工位后应用。" />

    <!-- 工具条 -->
    <div class="toolbar">
      <span class="dim">排班规则：</span>
      <el-radio-group v-model="preset" size="small" @change="onPresetChange">
        <el-radio-button v-for="p in presets" :key="p.key" :value="p.key">{{ p.label }}</el-radio-button>
        <el-radio-button value="custom" v-if="customDirty">自定义</el-radio-button>
      </el-radio-group>
      <el-tooltip :content="presets.find(p => p.key === preset)?.hint || '基于排班程序规则自定义'" placement="top">
        <el-icon class="dim"><QuestionFilled /></el-icon>
      </el-tooltip>
      <el-button type="primary" size="small" :loading="solving" @click="runPreview">
        <el-icon><MagicStick /></el-icon>&nbsp;运行智能排班（试算）
      </el-button>
      <el-button size="small" @click="$emit('reload')">刷新名单</el-button>
      <el-button size="small" type="success" plain @click="walkinVisible = true">补录志愿者</el-button>
      <el-button v-if="customDirty" size="small" link @click="resetCustom">放弃自定义</el-button>
      <span style="margin-left:auto" class="dim" v-if="preview">
        已确认 {{ preview.total_confirmed }} 人 · 建议 {{ preview.total_assigned }} 人上岗 ·
        <span v-if="preview.degraded" style="color:#e6a23c">规模较大，已自动降级为浅层算法</span>
      </span>
    </div>

    <!-- 高级规则自定义 -->
    <el-collapse v-model="advOpen" style="margin-bottom:12px">
      <el-collapse-item name="adv" :title="customDirty ? '高级规则自定义（已修改）' : '高级规则自定义'">
        <div class="rule-grid">
          <div>
            <div class="rule-cap">轮到谁挑（顺序即优先级）</div>
            <div class="rule-line" v-for="(r, i) in cfgOrder" :key="r">
              <el-icon class="mv" @click="move(cfgOrder, i, -1)"><Top /></el-icon>
              <el-icon class="mv" @click="move(cfgOrder, i, 1)"><Bottom /></el-icon>
              <span>{{ ORDER_LABELS[r] }}</span>
              <el-tag v-if="r === 'signup_time'" size="small" effect="plain" type="info">不可关闭</el-tag>
            </div>
            <el-checkbox v-model="cfgRoundCap" style="margin-top:4px">每轮每人至多新增一个岗位</el-checkbox>
          </div>
          <div>
            <div class="rule-cap">挑哪个岗位（候选排序）</div>
            <div class="rule-line" v-for="(r, i) in cfgCandidate" :key="r">
              <el-icon class="mv" @click="move(cfgCandidate, i, -1)"><Top /></el-icon>
              <el-icon class="mv" @click="move(cfgCandidate, i, 1)"><Bottom /></el-icon>
              <span>{{ CAND_LABELS[r] }}</span>
              <el-icon class="mv" style="color:#f56c6c" @click="cfgCandidate.splice(i,1)"><Close /></el-icon>
            </div>
            <el-select v-model="addCandidate" size="small" placeholder="添加规则…" style="width:200px"
                       v-if="cfgCandidate.length < CAND_ALL.length" @change="addCand">
              <el-option v-for="c in CAND_ALL.filter(x => !cfgCandidate.includes(x))"
                         :key="c" :value="c" :label="CAND_LABELS[c]" />
            </el-select>
          </div>
          <div>
            <div class="rule-cap">后处理开关</div>
            <el-checkbox v-model="cfgToggles.cohort_swap">同批换人（同批岗位尽量同一批人）</el-checkbox><br />
            <el-checkbox v-model="cfgToggles.preference_phase">优先条件逐档放行</el-checkbox>
            <div class="dim" style="margin-top:10px;line-height:1.8">
              固定规则（不可关闭）：锁定的行绝不改动、只排本人报过的岗位、必选条件必须满足、
              时间冲突不兼排、不超名额、已排的人不掉岗、组长须满足组长条件、平局按岗位表顺序。
            </div>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>

    <!-- 预览结果 -->
    <template v-if="preview">
      <el-alert type="info" :closable="false" style="margin-bottom:10px">
        <div style="line-height:1.8">
          <b>本次排班依据：</b>
          <div v-for="(line, i) in preview.rules_desc" :key="i">{{ line }}</div>
        </div>
      </el-alert>

      <!-- 工位统计 -->
      <div class="section-title">各工位建议人数</div>
      <el-table :data="preview.station_stats" size="small" border style="margin-bottom:14px">
        <el-table-column prop="name" label="工位" min-width="120" />
        <el-table-column prop="time" label="时间" min-width="130" />
        <el-table-column prop="location" label="地点" min-width="110" />
        <el-table-column prop="quota" label="名额" width="70" />
        <el-table-column label="建议排入" width="90">
          <template #default="{row}"><b>{{ row.suggested }}</b></template>
        </el-table-column>
        <el-table-column label="缺口" width="80">
          <template #default="{row}">
            <span :style="{color: row.quota - row.suggested > 0 ? '#e6a23c' : '#67c23a'}">
              {{ row.quota - row.suggested }}
            </span>
          </template>
        </el-table-column>
      </el-table>

      <div class="section-title">分配明细（可直接修改"建议工位"列再应用）</div>
      <el-table :data="rows" size="small" border max-height="420">
        <el-table-column label="姓名" width="90">
          <template #default="{row}"><b>{{ row.user.name }}</b></template>
        </el-table-column>
        <el-table-column prop="user.student_id" label="学号" width="110" />
        <el-table-column prop="user.college" label="学院" width="120" />
        <el-table-column label="报名时间" width="110">
          <template #default="{row}"><span class="dim">{{ (row.joined_at || '').slice(5,16).replace('T',' ') }}</span></template>
        </el-table-column>
        <el-table-column label="原工位" width="100">
          <template #default="{row}">{{ stationName(row.current_station_id) || '未分配' }}</template>
        </el-table-column>
        <el-table-column label="本人意愿" width="100">
          <template #default="{row}">
            <el-tooltip placement="top"
                        :content="row.allow_reassign ? '本人报名时表示接受岗位调剂'
                                                     : '本人报名时表示不接受调剂；仍可调整，会计入留痕'">
              <el-tag size="small" effect="plain"
                      :type="row.allow_reassign ? 'success' : 'info'">
                {{ row.allow_reassign ? '接受调剂' : '不接受' }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="建议工位" min-width="200">
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
            <el-tag v-else-if="row.target === row.current_station_id" size="small" type="success" effect="plain">保持</el-tag>
            <el-tag v-else-if="!row.current_station_id" size="small" type="primary" effect="plain">新排入</el-tag>
            <el-tag v-else size="small" :type="row.allow_reassign ? 'warning' : 'danger'"
                    effect="plain">
              {{ row.allow_reassign ? '调整' : '调整(违意愿)' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="超额强制" width="90">
          <template #default="{row}">
            <el-tooltip content="该工位名额已满仍要排入时勾选（计入留痕）" placement="top">
              <el-checkbox v-model="row.force" :disabled="!overQuota(row)" />
            </el-tooltip>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="preview.unassigned.length" class="section-title">
        暂未能分配（{{ preview.unassigned.length }} 人）
      </div>
      <el-tag v-for="u in preview.unassigned" :key="u.user.id" type="danger" effect="plain"
              style="margin-right:8px;margin-bottom:4px">
        {{ u.user.name }}（{{ u.user.student_id }}）· {{ u.reason }}
      </el-tag>

      <el-alert v-if="unwillingMoved" type="warning" :closable="false"
                style="margin-top:12px"
                :title="`有 ${unwillingMoved} 人报名时选择了不接受调剂，本方案仍会调换其工位`"
                description="可以继续发布——这只是提醒，操作会连同姓名一起记入留痕日志。" />

      <div style="margin-top:14px;text-align:right">
        <el-button @click="resetEdit">还原为系统建议</el-button>
        <el-button type="primary" :loading="applying" @click="apply">确认并发布分配</el-button>
        <div class="dim" style="margin-top:6px">
          发布后每位志愿者将通过企业微信收到「专属工位通知」（精确时间+地点）；操作计入留痕日志
        </div>
      </div>
    </template>

    <el-empty v-else description="点击「运行智能排班」开始二次分配——同一套算法驱动：先到先得、摊平、保底、条件过滤、冲突规避，规则可在上方自定义"
              :image-size="80" />

    <!-- 补录 -->
    <el-dialog v-model="walkinVisible" title="补录志愿者（现场添加）" width="430">
      <el-form label-width="90px">
        <el-form-item label="学号"><el-input v-model="walkin.sid" /></el-form-item>
        <el-form-item label="指定工位">
          <el-select v-model="walkin.station" clearable style="width:100%">
            <el-option v-for="s in stationOpts" :key="s.value" :value="s.value" :label="s.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="调剂意愿">
          <el-checkbox v-model="walkin.allowReassign">本人接受岗位调剂</el-checkbox>
        </el-form-item>
        <el-form-item label="">
          <el-checkbox v-model="walkin.force">
            时段冲突或名额已满时强制加入（计入留痕）
          </el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="walkinVisible = false">取消</el-button>
        <el-button type="primary" @click="addWalkin">确认补录</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const props = defineProps({
  group: { type: Object, required: true },
  unassignedCount: { type: Number, default: 0 },   // 已确认但还没有工位的人数
})
const emit = defineEmits(['reload'])

const loading = ref(false)
const solving = ref(false)
const applying = ref(false)
const preset = ref('balanced')
const customDirty = ref(false)
const advOpen = ref([])
const preview = ref(null)
const rows = ref([])
const walkinVisible = ref(false)
const walkin = ref({ sid: '', station: '', force: false, allowReassign: true })

const presets = [
  { key: 'balanced', label: '均衡（推荐）', hint: '所有人拿到第1个岗位之前，没人拿第2个；适合大多数活动' },
  { key: 'first_come', label: '先到先得优先', hint: '早报名的人一次把他报的岗位拿满' },
  { key: 'floor_first', label: '保底优先', hint: '死保每个工位的最低人数，代价是换岗的人会多' },
]

const ORDER_LABELS = {
  round_cap: '每轮每人至多一个岗位（结构开关，见下方勾选）',
  spread: '已获岗位数少者优先（摊平）',
  signup_time: '报名时间早者优先（先到先得）',
  few_options: '报名岗位数少者优先',
}
const CAND_LABELS = {
  min_quota: '未达最低人数的工位优先（保底）',
  gap: '缺口率大的工位优先',
  cohort: '同批：优先与已获伙伴同工位',
  stable: '上一版组合优先（增量稳定）',
  scarce: '别人补不上的工位优先',
  preference: '符合优先条件的靠前',
}
const ORDER_ALL = ['spread', 'signup_time', 'few_options']
const CAND_ALL = ['min_quota', 'gap', 'cohort', 'stable', 'scarce', 'preference']

const cfgRoundCap = ref(true)
const cfgOrder = ref([...ORDER_ALL])
const cfgCandidate = ref([...CAND_ALL])
const cfgToggles = ref({ cohort_swap: true, preference_phase: true })
const addCandidate = ref('')

// 精细化模式且人人有岗 → 提示即可，不禁止试算
const fineModeAllAssigned = computed(() =>
  !props.group.needs_assign && props.unassignedCount === 0)

const stationOpts = computed(() =>
  props.group.stations.map(s => ({
    value: s.id, label: s.name,
    left: Math.max(0, s.quota - (s.confirmed || 0)),
  })))

function stationName(sid) {
  const s = props.group.stations.find(x => x.id === sid)
  return s ? s.name : ''
}

// 本人不接受调剂、方案又把他挪出原工位的人数（只提示，不拦）
const unwillingMoved = computed(() => rows.value.filter(
  r => !r.allow_reassign && r.current_station_id && r.target
       && r.target !== r.current_station_id).length)

function onPresetChange() {
  if (preset.value !== 'custom') {
    customDirty.value = false
    const spec = {
      balanced: { order: ['spread', 'signup_time', 'few_options'], round: true,
                  cand: [...CAND_ALL], tog: { cohort_swap: true, preference_phase: true } },
      first_come: { order: ['signup_time', 'spread'], round: false,
                    cand: ['gap', 'cohort', 'stable', 'scarce', 'preference', 'min_quota'],
                    tog: { cohort_swap: true, preference_phase: true } },
      floor_first: { order: ['spread', 'signup_time', 'few_options'], round: true,
                     cand: ['min_quota', 'gap', 'cohort', 'stable', 'scarce', 'preference'],
                     tog: { cohort_swap: true, preference_phase: true } },
    }[preset.value]
    if (spec) {
      cfgOrder.value = spec.order
      cfgRoundCap.value = spec.round
      cfgCandidate.value = spec.cand
      cfgToggles.value = spec.tog
    }
  }
}
function resetCustom() {
  customDirty.value = false
  preset.value = 'balanced'
  onPresetChange()
}
function move(arr, i, d) {
  const j = i + d
  if (j < 0 || j >= arr.length) return
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
  markCustom()
}
function addCand() {
  if (addCandidate.value) {
    cfgCandidate.value.push(addCandidate.value)
    addCandidate.value = ''
    markCustom()
  }
}
function markCustom() {
  customDirty.value = true
  preset.value = 'custom'
}

function buildRulesBody() {
  if (!customDirty.value && preset.value !== 'custom') {
    return { preset: preset.value, rules: null }
  }
  if (!cfgOrder.value.includes('signup_time')) {
    // 先到先得不可关闭（排班程序硬性要求）
    cfgOrder.value = ['signup_time', ...cfgOrder.value]
  }
  const order = [...cfgOrder.value]
  if (cfgRoundCap.value) order.unshift('round_cap')
  return {
    preset: 'custom',
    rules: {
      preset: 'custom', order,
      candidate: [...cfgCandidate.value],
      toggles: { ...cfgToggles.value },
    },
  }
}

// 编辑态行
function buildRows() {
  rows.value = preview.value.rows.map(r => ({
    ...r, target: r.suggested_station_id || '', force: false,
    over: false,
  }))
}
function resetEdit() { buildRows() }
function overQuota(row) {
  const s = props.group.stations.find(x => x.id === row.target)
  return s ? s.quota - (s.confirmed || 0) <= 0 : false
}

async function runPreview() {
  if (!props.group.stations.length) {
    ElMessage.warning('请先在「岗位树」页签下创建二级工位，再运行智能排班')
    return
  }
  solving.value = true
  try {
    const body = buildRulesBody()
    preview.value = await api.post(
      `/api/staff/groups/${props.group.id}/assign/preview`, body, {})
    buildRows()
  } finally { solving.value = false }
}

async function apply() {
  const fullPlan = rows.value.map(r => ({
    enrollment_id: r.enrollment_id, station_id: r.target, force: r.force }))
  applying.value = true
  try {
    const d = await api.post(`/api/staff/groups/${props.group.id}/assign/apply`,
      { plan: fullPlan, source: 'assign' })
    ElMessage.success(d.summary + (d.applied ? '，通知已发送' : ''))
    preview.value = null
    emit('reload')
  } catch (e) {
    ElMessage.error(e.message + '——可在对应行勾选「强制」后重试')
  } finally { applying.value = false }
}

async function addWalkin() {
  try {
    await api.post(`/api/staff/groups/${props.group.id}/add-walkin`, {
      student_id: walkin.value.sid, station_id: walkin.value.station || '',
      allow_reassign: walkin.value.allowReassign,
      force: walkin.value.force })
    ElMessage.success('补录成功')
    walkinVisible.value = false
    walkin.value = { sid: '', station: '', force: false, allowReassign: true }
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
.toolbar { display:flex; gap:10px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }
.rule-grid { display:grid; grid-template-columns: 1fr 1fr 1fr; gap:18px; }
.rule-cap { font-weight:600; font-size:13px; margin-bottom:8px; }
.rule-line { display:flex; align-items:center; gap:6px; padding:3px 0; font-size:13px; }
.mv { cursor:pointer; color:#909399; }
.mv:hover { color:var(--el-color-primary); }
</style>
