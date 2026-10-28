<template>
  <div class="page page-wide" v-loading="loading" style="min-height:300px">
    <div class="page-head">
      <h2>数据总览</h2>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD"
                        :shortcuts="shortcuts" start-placeholder="开始日期"
                        end-placeholder="结束日期"
                        :style="{ width: narrow ? '100%' : '320px' }" />
        <el-button type="primary" @click="exportSummary">导出活动汇总</el-button>
      </div>
    </div>
    <!-- 接口失败时卡片上显示「—」并给出提示：留着 0 看上去就像真的一个都没有 -->
    <el-alert v-if="failed" type="error" :closable="false" show-icon
              title="数据没有取到，卡片上的数暂不可用" style="margin-bottom:16px">
      <el-button size="small" @click="load">重新加载</el-button>
    </el-alert>
    <!-- 卡片先立起来（标题 + 0），数据回来再从 0 滚上去：等接口的这几百毫秒里
         页面不至于是一片空白，谁在加载、有哪些数一眼就知道 -->
    <el-row :gutter="16">
      <el-col :span="3" v-for="c in cards" :key="c.label">
        <el-card shadow="never" class="kpi">
          <div class="v" :style="{ color: c.color }">{{ c.value }}</div>
          <div class="dim">{{ c.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top:16px" v-if="d && !failed">
      <el-col :span="15">
        <el-card shadow="never"><template #header><b>近14天报名趋势</b></template>
          <div ref="trendEl" style="height:260px"></div>
        </el-card>
      </el-col>
      <el-col :span="9">
        <el-card shadow="never"><template #header><b>报名学院分布</b></template>
          <div ref="pieEl" style="height:260px"></div>
        </el-card>
      </el-col>
    </el-row>
    <el-row :gutter="16" style="margin-top:16px" v-if="d && !failed">
      <el-col :span="24">
        <el-card shadow="never">
          <template #header><b>最近操作留痕</b>
            <router-link to="/audit" style="float:right;font-size:13px">全部 →</router-link>
          </template>
          <DataCards v-if="narrow" :rows="d.recent_logs" title-key="summary"
                 :fields="[{ key: 'actor_name', label: '操作人' },
                           { key: 'created_at', label: '时间', format: fmtDT }]"
                 empty="暂无操作记录" />
      <el-table v-else :data="d.recent_logs" size="small">
            <el-table-column label="操作人" width="90" prop="actor_name" />
            <el-table-column label="摘要" min-width="300" prop="summary" show-overflow-tooltip />
            <el-table-column label="时间" width="180">
              <template #default="{row}">{{ fmtDT(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { api, download, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from '../components/DataCards.vue'

const narrow = useNarrow()
const d = ref(null)
const range = ref([])          // 页面与导出共用的时间跨度，空 = 不限
const loading = ref(false)
const failed = ref(false)      // 最近一次取数失败：卡片显示「—」，图表和留痕不显示
const trendEl = ref()
const pieEl = ref()

/** 七张卡：取值字段、标题、颜色、小数位。数组顺序就是页面上的顺序。 */
const CARDS = [
  { key: 'students', label: '注册学生', color: '#1f5aa8', digits: 0 },
  { key: 'activities_published', label: '报名中活动', color: '#ae0b2a', digits: 0 },
  { key: 'activities_all', label: '活动总数', color: '#606266', digits: 0 },
  { key: 'active_enrollments', label: '有效报名', color: '#1e7a52', digits: 0 },
  { key: 'waitlisted', label: '候补中', color: '#e6a23c', digits: 0 },
  { key: 'checked_recent', label: '24h内签到人次', color: '#7a4bb3', digits: 0 },
  { key: 'hours_total', label: '累计时长(h)', color: '#b3661f', digits: 1 },
]

/** 卡片上此刻显示的数值，roll() 每帧改写它；模板经 cards 读。 */
const shown = reactive(Object.fromEntries(CARDS.map(c => [c.key, 0])))  // vuecheck: script-only

const cards = computed(() => CARDS.map(c => ({
  label: c.label, color: c.color,
  value: failed.value ? '—' : shown[c.key].toFixed(c.digits),
})))

/** 快捷跨度：本月、上个月、近 3 个月，都算到本月这天为止。 */
const shortcuts = [
  { text: '本月', value: () => {
      const t = new Date()
      return [new Date(t.getFullYear(), t.getMonth(), 1), t]
    } },
  { text: '上个月', value: () => {
      const t = new Date()
      return [new Date(t.getFullYear(), t.getMonth() - 1, 1),
              new Date(t.getFullYear(), t.getMonth(), 0)]
    } },
  { text: '近 3 个月', value: () => {
      const t = new Date()
      return [new Date(t.getFullYear(), t.getMonth() - 2, 1), t]
    } },
]

/** 当前区间拼成的 query：只带选了的那一端，都没选就是空串（不限）。
 *  页面重取与导出都用它，两边永远是同一段区间。 */
function rangeQuery() {
  const [start, end] = range.value || []
  const q = new URLSearchParams()
  if (start) q.set('start', start)
  if (end) q.set('end', end)
  const s = q.toString()
  return s ? `?${s}` : ''
}

// 走 download 而不是 downloadExport：后者先问一次 attachments 有几个附件，
// 这张汇总表没有附件接口，那一问会落空，表也就下不来了
function exportSummary() {
  download(`/api/staff/activities-summary.xlsx${rangeQuery()}`)
}

/** 拿容器上已有的图表实例，第一次画才新建。
 *
 *  每次画都 init，同一个 div 上就会叠出第二个实例：先建的那个还占着容器，
 *  监听和动画都留着，新图只是盖上去，切几次区间越积越多。
 */
function chartOf(el) {
  return echarts.getInstanceByDom(el) || echarts.init(el)
}

function draw() {
  const axis = { axisLabel: { fontSize: 10 }, grid: { left: 40, right: 16, top: 20, bottom: 26 } }
  chartOf(trendEl.value).setOption({
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: d.value.enroll_trend.map(x => x.date) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{ type: 'line', smooth: true, areaStyle: { opacity: .12 },
               data: d.value.enroll_trend.map(x => x.count),
               itemStyle: { color: '#ae0b2a' } }],
    ...axis,
  }, true)
  // notMerge：换区间后掉出前八的学院要跟着消失，留着就是一张和数字对不上的饼
  chartOf(pieEl.value).setOption({
    tooltip: { trigger: 'item' },
    series: [{ type: 'pie', radius: ['38%', '68%'],
      data: d.value.colleges.map(x => ({ name: x.college, value: x.count })),
      label: { fontSize: 10 } }],
  }, true)
}

/** 数字滚动：进页面时从 0 缓出到目标值，之后改区间就从当前值滚到新值。
 *
 *  用 requestAnimationFrame 而不是 CSS 过渡，是因为动的是数值本身，CSS 只会
 *  整体淡入淡出。缓出（ease-out）让末段慢下来，读的人跟得上落点。
 *  系统开了「减少动态效果」就直接落值：连续跳动的数字对这类人是负担。
 */
const ROLL_MS = 750
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
let firstRoll = true
let rollRAF = 0

function roll() {
  cancelAnimationFrame(rollRAF)
  rollRAF = 0
  const targets = {}
  const from = {}
  for (const c of CARDS) {
    targets[c.key] = Number(d.value.cards[c.key]) || 0
    // 首屏从 0 滚上去；换区间从当前值滚到新值——不随区间变的卡不会先跳回 0
    from[c.key] = firstRoll ? 0 : shown[c.key]
  }
  firstRoll = false
  if (reduceMotion.matches) {
    for (const c of CARDS) shown[c.key] = targets[c.key]
    return
  }
  const t0 = performance.now()
  const step = (now) => {
    const p = Math.min((now - t0) / ROLL_MS, 1)
    const k = 1 - (1 - p) ** 3          // 三次缓出：起步快，末段贴着目标值停住
    for (const c of CARDS) {
      shown[c.key] = from[c.key] + (targets[c.key] - from[c.key]) * k
    }
    rollRAF = p < 1 ? requestAnimationFrame(step) : 0
  }
  rollRAF = requestAnimationFrame(step)
}

let seq = 0                            // 请求序号：迟到的响应盖不掉后发起的那次

/** 取整页数据：区间一变就重取，四张卡与学院分布跟着区间走，其余字段不随区间变、
 *  后端一并返回。画图要等 nextTick：图表的容器在 v-if="d" 里，这一帧还没渲染出来。 */
async function load() {
  const mine = ++seq
  loading.value = true
  try {
    const data = await api.get(`/api/staff/overview${rangeQuery()}`)
    if (mine !== seq) return          // 又选了新的区间，这次的结果已经过期
    d.value = data
    failed.value = false
    await nextTick()
    draw()
    roll()
  } catch {
    // 弹窗提示由 api 层统一弹出；页面上也要标出这次没取到，别让旧数或 0 冒充结果
    if (mine === seq) {
      cancelAnimationFrame(rollRAF)
      rollRAF = 0
      failed.value = true
    }
  } finally {
    if (mine === seq) loading.value = false
  }
}

watch(rangeQuery, load)                // 区间改了、清空了都重取；空区间与没选是同一个 query
onMounted(load)
</script>

<style scoped>
.kpi { text-align: center; border-radius: 4px; }
.kpi .v { font-size: 26px; font-weight: 700; line-height: 1.3; }
</style>
