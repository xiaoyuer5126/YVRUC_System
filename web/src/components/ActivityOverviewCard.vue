<template>
  <el-card shadow="never" class="ov-card">
    <div class="ov-head">
      <b>活动概览</b>
      <RefreshBar size="small" :load="load" :at="at" />
    </div>
    <div v-if="!d" class="dim">{{ failed ? '概览加载失败，点「刷新」重试' : '正在加载…' }}</div>
    <template v-else>
      <div class="ov-kpis">
        <div v-for="k in kpis" :key="k.label" class="ov-kpi"
             :class="[k.tone, { link: k.open }]" @click="k.open?.()">
          <div class="ov-v">{{ k.value }}</div>
          <div class="dim">{{ k.label }}<template v-if="k.open"> ›</template></div>
          <div v-if="k.sub" class="ov-sub dim">{{ k.sub }}</div>
        </div>
      </div>

      <!-- 各场次报满情况：只列服务场次，培训/彩排不招人。每场一张卡片，
           宽屏一排三四张，手机上一列 -->
      <div v-if="!d.record && d.shifts.length" class="ov-shifts">
        <div class="ov-title">各场次报满情况（{{ fullShifts }}/{{ d.shifts.length }} 场已满）</div>
        <div class="ov-grid">
          <div v-for="g in d.shifts" :key="g.id" class="ov-shift">
            <div class="ov-row">
              <span class="ov-name"><b>{{ g.name }}</b> <span class="dim">{{ g.time }}</span></span>
              <span class="ov-tags">
                <el-tag v-if="g.full" size="small" type="success">已满</el-tag>
                <el-tag v-if="g.paused" size="small" type="warning">暂停报名</el-tag>
                <el-tag v-if="g.locked" size="small" type="info">名单已锁定</el-tag>
                <el-tag v-if="g.waitlist" size="small" type="info">候补 {{ g.waitlist }}</el-tag>
                <el-tag v-if="g.unassigned" size="small" type="info" effect="plain">
                  待分配 {{ g.unassigned }}
                </el-tag>
              </span>
            </div>
            <div class="ov-bar">
              <el-progress :percentage="pct(g.occupied, g.capacity)" :stroke-width="8"
                           :status="g.full ? 'success' : ''" :show-text="false" />
              <span class="ov-num">{{ g.occupied }}/{{ g.capacity }}</span>
            </div>
            <div v-for="p in g.positions" :key="p.id" class="ov-pos">
              <span class="ov-pname">
                {{ p.group ? p.group + ' · ' : '' }}{{ p.name }}
                <el-tag v-if="p.paused" size="small" type="warning" effect="plain">暂停</el-tag>
                <el-tag v-if="p.locked" size="small" type="info" effect="plain">锁定</el-tag>
                <span v-if="p.waitlist" class="dim">候补 {{ p.waitlist }}</span>
              </span>
              <div class="ov-bar">
                <el-progress :percentage="pct(p.occupied, p.quota)" :stroke-width="6"
                             :status="p.full ? 'success' : ''" :show-text="false" />
                <span class="ov-num">{{ p.occupied }}/{{ p.quota }}</span>
              </div>
            </div>
          </div>
        </div>
        <div class="dim ov-note">
          名额占用含转正待确认的名额；截止后分配的场次按整场名额算，
          岗位一行是已分到该岗位的人数。
        </div>
      </div>
    </template>
    <ViolationListDialog ref="vioDlg" :activity-id="activityId" @changed="load" />
  </el-card>
</template>

<script setup>
/**
 * 活动管理页「概览」页签：报名、签到、时长、违规几个数，加各场次报满情况。
 * 口径写在后端接口上（staff_ops.activity_overview）。不自动轮询，跟页上其他
 * 现场数据一样点「刷新」才拉；每次切回这个页签时父组件也会调一次 load。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import RefreshBar from './RefreshBar.vue'
import ViolationListDialog from './ViolationListDialog.vue'

const props = defineProps({ activityId: { type: String, required: true } })

const d = ref(null)
const at = ref(0)
const vioDlg = ref(null)
const failed = ref(false)

async function load() {
  try {
    d.value = await api.get(`/api/staff/activities/${props.activityId}/overview`,
                            { silent: true })
    at.value = Date.now()
    failed.value = false
  } catch {
    failed.value = true
  }
}
onMounted(load)
defineExpose({ load })

const pct = (n, cap) => (cap ? Math.min(100, Math.round((n / cap) * 100)) : 0)
const fmtH = h => `${+Number(h).toFixed(2)}`
const fullShifts = computed(() => d.value?.shifts?.filter(g => g.full).length || 0)

const kpis = computed(() => {
  const x = d.value
  if (!x) return []
  const h = x.hours
  const hours = {
    label: h.approved ? '已核准时长(h)' : '已确认时长(h)·未核准',
    value: fmtH(h.total),
    sub: h.party_school ? `含党校同学 ${fmtH(h.party_school)}` : '',
  }
  // 有违规才能点开：列出本活动全部违规，登记错了在那里撤
  const vio = { label: '违规记录', value: x.violations, tone: x.violations ? 'warn' : '',
                open: x.violations ? () => vioDlg.value?.open() : null }
  if (x.record) return [{ label: '名单人数', value: x.roster }, hours, vio]
  const e = x.enroll, c = x.checkin
  return [
    { label: '报名人次 / 总名额', value: `${e.count} / ${e.capacity}`,
      sub: `去重 ${e.people} 人` + (e.applied ? ` · 待筛选 ${e.applied}` : '') },
    { label: '候补', value: e.waitlist },
    { label: '签到率', value: c.rate === null ? '—' : `${Math.round(c.rate * 1000) / 10}%`,
      sub: c.due ? `${c.arrived}/${c.due}（已开始场次）` : '还没有场次开始' },
    { label: '未签退', value: c.not_checked_out, tone: c.not_checked_out ? 'warn' : '' },
    hours,
    vio,
    { label: '锁定后退出', value: x.locked_quit, tone: x.locked_quit ? 'warn' : '' },
  ]
})
</script>

<style scoped>
.ov-card { border-radius: 4px; }
.ov-head { display: flex; align-items: center; justify-content: space-between;
           gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.ov-kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); gap: 8px; }
.ov-kpi { background: #f7f8fa; border-radius: 4px; padding: 8px 12px; min-width: 0; }
.ov-kpi.warn .ov-v { color: var(--el-color-danger); }
.ov-kpi.link { cursor: pointer; }
.ov-kpi.link:hover { background: #eef1f6; }
.ov-v { font-size: 20px; font-weight: 700; line-height: 1.4; }
.ov-sub { font-size: 12px; }
.ov-shifts { margin-top: 16px; }
.ov-title { font-weight: 600; margin-bottom: 8px; }
.ov-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr)); gap: 12px; }
.ov-shift { border: 1px solid #eceef1; border-radius: 4px; padding: 8px 12px; min-width: 0; }
.ov-row { display: flex; align-items: center; justify-content: space-between;
          gap: 8px; flex-wrap: wrap; }
.ov-name { min-width: 0; }
.ov-tags { display: inline-flex; gap: 4px; flex-wrap: wrap; }
.ov-bar { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
.ov-bar .el-progress { flex: 1; min-width: 0; }
.ov-num { font-variant-numeric: tabular-nums; font-size: 12px; min-width: 48px; text-align: right; }
.ov-pos { padding-left: 12px; margin-top: 4px; font-size: 13px; }
.ov-pname { display: inline-flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.ov-note { font-size: 12px; margin-top: 8px; }
</style>
