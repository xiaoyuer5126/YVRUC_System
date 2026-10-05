<template>
  <div class="page" v-if="data">
    <h2>我的志愿时长</h2>
    <el-row :gutter="16" style="margin-bottom:16px">
      <el-col :span="8">
        <el-card shadow="never" class="stat">
          <div class="big">{{ data.total }}</div>
          <div class="dim">累计时长（小时）</div>
          <div v-if="data.records.some(r => r.party_school)" class="dim">党校的时长不计入</div>
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
      <template #header><b>时长明细</b><span class="dim" style="margin-left:8px">
        由负责人核准，展开一行可看这次时长由哪几段组成。
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
          <el-tag v-if="row.party_school" effect="plain" size="small">党校</el-tag>
          <el-tag :type="stateOf(row).type" effect="light" size="small">
            {{ stateOf(row).text }}
          </el-tag>
          <AppealTag :row="row" />
          <HoursParts :parts="row.parts" style="margin-top:8px" />
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
        <el-table-column label="活动" min-width="200">
          <template #default="{ row }">
            {{ row.activity_title }}
            <el-tag v-if="row.party_school" effect="plain" size="small"
                    style="margin-left:4px">党校</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时长（小时）" width="110">
          <template #default="{ row }"><b>{{ row.hours }}</b></template>
        </el-table-column>
        <el-table-column label="状态" width="290">
          <template #default="{ row }">
            <el-tag :type="stateOf(row).type" effect="light">{{ stateOf(row).text }}</el-tag>
            <span v-if="row.can_confirm || row.can_appeal" class="dim" style="margin-left:8px">
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
            <div class="btn-row">
              <el-button v-if="row.can_confirm" size="small" type="primary"
                         :plain="!!row.confirmed_at" @click="openConfirm(row)">
                {{ row.confirmed_at ? '改报酬方式' : '确认无误' }}
              </el-button>
              <el-button v-if="row.can_appeal" size="small" plain @click="openAppeal(row)">
                有异议
              </el-button>
              <span v-if="!row.can_confirm && !row.can_appeal" class="dim">—</span>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!data.records.length" description="参加活动并完成服务后，将会显示时长" />
    </el-card>

    <el-dialog v-model="cf.visible" title="确认时长" width="480">
      <p class="dim">
        「{{ cf.row?.activity_title }}」记 <b>{{ cf.row?.hours }}</b> 小时。确认后到截止前仍可改。
      </p>
      <!-- 报酬方式按段选：只有岗位开了劳务的段能改，其余段只记志愿时长 -->
      <template v-if="cf.row?.pay_allowed">
        <div class="dim" style="margin-bottom:8px">报酬方式（选劳务的段不记志愿时长）</div>
        <div v-for="(p, i) in cf.parts" :key="i" class="seg">
          <div class="seg-head">
            <span>{{ p.label || '手动补录' }}</span>
            <span v-if="p.time" class="dim">{{ p.time }}</span>
            <span v-if="p.position" class="dim">{{ p.position }}</span>
            <b>{{ p.hours }}h</b>
          </div>
          <template v-if="p.pay_ok">
            <el-radio-group v-model="p.reward" size="small">
              <el-radio value="hours">志愿时长</el-radio>
              <el-radio value="pay">劳务</el-radio>
            </el-radio-group>
            <div v-if="p.pay_note" class="dim">{{ p.pay_note }}</div>
          </template>
          <el-tag v-else size="small" effect="plain" type="success">志愿时长</el-tag>
        </div>
      </template>
      <template #footer>
        <el-button @click="cf.visible = false">取消</el-button>
        <el-button type="primary" :loading="cf.busy" @click="submitConfirm">确认无误</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dlg.visible" title="时长有异议" width="480">
      <p class="dim">
        「{{ dlg.row?.activity_title }}」当前记 <b>{{ dlg.row?.hours }}</b> 小时。
        写清楚实际的到离时间，负责人核对后决定是否改。
      </p>
      <el-form label-width="88px">
        <el-form-item label="申报时长" required>
          <el-input-number v-model="dlg.hours" :min="0.5" :max="24" :step="0.5" />
          <span class="dim" style="margin-left:12px">小时</span>
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
    ? h(ElTooltip, { content: a.reply, placement: 'top', popperClass: 'pre-tip' }, () => tag)
    : tag
}

const cf = ref({ visible: false, row: null, parts: [], busy: false })
function openConfirm(row) {
  cf.value = { visible: true, row, parts: row.parts.map(p => ({ ...p })), busy: false }
}
async function submitConfirm() {
  const c = cf.value
  c.busy = true
  try {
    const parts = c.parts.filter(p => p.pay_ok)
      .map(p => ({ shift_id: p.shift_id, position_id: p.position_id, reward: p.reward }))
    const d = await api.post(`/api/me/hours/${c.row.id}/confirm`, { parts })
    ElMessage.success(`已确认：${d.reward_label}`)
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
    ElMessage.success('已提交，负责人处理后会通知你')
    d.visible = false
    load()
  } finally { d.busy = false }
}

// 还没核准（本人只当参考）→ 已核准待表态 / 已确认 → 已锁定，账结了
function stateOf(row) {
  const how = row.reward_label
  if (row.locked) return { type: 'success', text: `已锁定 · ${how}` }
  if (row.approved && row.confirmed_at) return { type: 'success', text: `已确认 · ${how}` }
  if (row.approved) return { type: 'warning', text: '已核准请确认，如有异议请申诉' }
  return { type: 'info', text: '待核准' }
}
</script>

<style scoped>
.stat { text-align: center; border-radius: 14px; }
.big { font-size: 34px; font-weight: 700; color: var(--ruc-red); }
.seg { display: flex; flex-direction: column; gap: 4px; padding: 8px 0;
       border-top: 1px solid var(--el-border-color-lighter); }
.seg-head { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; font-size: 13px; }
.seg-head b { color: var(--el-color-primary); }
</style>
