<template>
  <div class="page" v-if="data">
    <h2>我的志愿时长</h2>
    <el-row :gutter="16" style="margin-bottom:16px">
      <el-col :span="8">
        <el-card shadow="never" class="stat">
          <div class="big">{{ data.total }}</div>
          <div class="dim">累计时长（小时）</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="stat">
          <div class="big">{{ data.locked }}</div>
          <div class="dim">已锁定（小时）</div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="stat">
          <div class="big">{{ data.records.length }}</div>
          <div class="dim">参与活动场次</div>
        </el-card>
      </el-col>
    </el-row>
    <el-card shadow="never">
      <template #header><b>时长明细</b><span class="dim" style="font-weight:400;margin-left:8px">
        依据签到记录，由负责老师核准；展开一行可看这次时长由哪几段组成。
        核准后请在截止前确认无误，数值有出入可以提异议；到点没表态视为确认无误、按志愿时长记
      </span></template>
      <DataCards v-if="narrow" :rows="data.records" title-key="activity_title"
                 :fields="[{ key: 'created_at', label: '记录时间', format: fmtDT },
                           { key: 'appeal_deadline', label: '申诉截止',
                             format: fmtDT, hideEmpty: true }]"
                 empty="还没有时长记录">
        <template #extra="{ row }">
          <b style="font-size:16px;color:var(--el-color-primary)">{{ row.hours }}h</b>
        </template>
        <template #body="{ row }">
          <el-tag :type="stateOf(row).type" effect="light" size="small">
            {{ stateOf(row).text }}
          </el-tag>
          <AppealTag :row="row" />
          <HoursParts :parts="row.parts" style="margin-top:6px" />
        </template>
        <template #actions="{ row }">
          <el-button v-if="row.can_confirm" size="small" type="primary"
                     :plain="!!row.confirmed_at" @click="openConfirm(row)">
            {{ row.confirmed_at ? '改报酬方式' : '确认无误' }}
          </el-button>
          <el-button v-if="row.can_appeal" size="small" plain @click="openAppeal(row)">
            时长有异议
          </el-button>
        </template>
      </DataCards>
      <el-table v-else :data="data.records">
        <el-table-column type="expand" width="30">
          <template #default="{ row }"><HoursParts :parts="row.parts" style="margin-left:30px" /></template>
        </el-table-column>
        <el-table-column prop="activity_title" label="活动" min-width="200" />
        <el-table-column label="时长（小时）" width="110">
          <template #default="{ row }"><b>{{ row.hours }}</b></template>
        </el-table-column>
        <el-table-column label="状态" width="290">
          <template #default="{ row }">
            <el-tag :type="stateOf(row).type" effect="light">{{ stateOf(row).text }}</el-tag>
            <span v-if="row.can_confirm || row.can_appeal" class="dim" style="margin-left:6px">
              截止 {{ fmtDT(row.appeal_deadline) }}
            </span>
            <AppealTag :row="row" />
          </template>
        </el-table-column>
        <el-table-column label="记录时间" width="130">
          <template #default="{ row }">{{ fmtDT(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button v-if="row.can_confirm" size="small" type="primary"
                       :plain="!!row.confirmed_at" @click="openConfirm(row)">
              {{ row.confirmed_at ? '改报酬方式' : '确认无误' }}
            </el-button>
            <el-button v-if="row.can_appeal" size="small" plain @click="openAppeal(row)">
              有异议
            </el-button>
            <span v-if="!row.can_confirm && !row.can_appeal" class="dim">—</span>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!data.records.length" description="参加活动并完成签到后，时长会出现在这里" :image-size="80" />
    </el-card>

    <el-dialog v-model="cf.visible" title="确认时长" width="440">
      <p class="dim" style="margin-top:0;line-height:1.8">
        「{{ cf.row?.activity_title }}」记 <b>{{ cf.row?.hours }}</b> 小时。确认后到截止前仍可改。
      </p>
      <el-form label-width="88px">
        <el-form-item label="报酬方式">
          <el-radio-group v-model="cf.reward">
            <el-radio value="hours">记志愿时长</el-radio>
            <el-radio value="pay" :disabled="!cf.row?.pay_allowed">劳务（不记时长）</el-radio>
          </el-radio-group>
          <div v-if="cf.row?.pay_allowed" class="dim">{{ cf.row.pay_note }}</div>
          <div v-else class="dim">本岗位只记志愿时长</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="cf.visible = false">取消</el-button>
        <el-button type="primary" :loading="cf.busy" @click="submitConfirm">确认无误</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dlg.visible" title="时长有异议" width="480">
      <p class="dim" style="margin-top:0;line-height:1.8">
        「{{ dlg.row?.activity_title }}」当前记 <b>{{ dlg.row?.hours }}</b> 小时。
        写清楚实际的到离时间，负责老师核对后决定是否改。
      </p>
      <el-form label-width="88px">
        <el-form-item label="申报时长" required>
          <el-input-number v-model="dlg.hours" :min="0.5" :max="24" :step="0.5" />
          <span class="dim" style="margin-left:10px">小时</span>
        </el-form-item>
        <el-form-item label="说明" required>
          <el-input v-model="dlg.reason" type="textarea" :rows="3" maxlength="500"
                    show-word-limit
                    placeholder="例：忘记签退，实际 17:30 离场；或：14:00-15:00 被叫去搬物资，不在岗" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="dlg.busy" @click="submitAppeal">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { h, onMounted, ref } from 'vue'
import { ElMessage, ElTag, ElTooltip } from 'element-plus'
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from '../components/DataCards.vue'
import HoursParts from '../components/HoursParts.vue'

const narrow = useNarrow()
const data = ref(null)
const load = async () => { data.value = await api.get('/api/me/hours') }
onMounted(load)

// 申诉状态标签：三种状态各一句话，鼠标悬停看老师的答复
const TYPES = { pending: 'info', accepted: 'success', rejected: 'danger' }
const AppealTag = (props) => {
  const a = props.row.appeal
  if (!a) return null
  const tag = h(ElTag, { size: 'small', effect: 'plain', type: TYPES[a.status],
                         style: 'margin-left:6px' },
                () => `申诉${a.label}`)
  return a.reply
    ? h(ElTooltip, { content: a.reply, placement: 'top' }, () => tag)
    : tag
}

const cf = ref({ visible: false, row: null, reward: 'hours', busy: false })
function openConfirm(row) {
  cf.value = { visible: true, row, reward: row.reward || 'hours', busy: false }
}
async function submitConfirm() {
  const c = cf.value
  c.busy = true
  try {
    const d = await api.post(`/api/me/hours/${c.row.id}/confirm`, { reward: c.reward })
    ElMessage.success(`已确认，按${d.reward_label}记`)
    c.visible = false
    load()
  } finally { c.busy = false }
}

const dlg = ref({ visible: false, row: null, hours: 1, reason: '', busy: false })

function openAppeal(row) {
  dlg.value = { visible: true, row, hours: row.hours, reason: '', busy: false }
}

async function submitAppeal() {
  const d = dlg.value
  if (d.reason.trim().length < 5) return ElMessage.warning('请把情况写清楚（至少 5 个字）')
  d.busy = true
  try {
    await api.post(`/api/me/hours/${d.row.id}/appeal`,
                   { claimed_hours: d.hours, reason: d.reason })
    ElMessage.success('已提交，负责老师处理后会通知你')
    d.visible = false
    load()
  } finally { d.busy = false }
}

// 还没核准（本人只当参考）→ 已核准待表态 / 已确认 → 已锁定，账结了
function stateOf(row) {
  const how = row.reward === 'pay' ? '劳务' : '志愿时长'
  if (row.locked) return { type: 'success', text: `已锁定 · ${how}` }
  if (row.approved && row.confirmed_at) return { type: 'success', text: `已确认 · ${how}` }
  if (row.approved) return { type: 'warning', text: '已核准，请确认或申诉' }
  return { type: 'info', text: '待核准' }
}
</script>

<style scoped>
.stat { text-align: center; border-radius: 14px; }
.big { font-size: 34px; font-weight: 700; color: var(--ruc-red); }
</style>
