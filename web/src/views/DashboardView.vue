<template>
  <div class="page page-wide">
    <h2>数据总览</h2>
    <div class="toolbar">
      <el-date-picker v-model="range" type="daterange" value-format="YYYY-MM-DD"
                      :shortcuts="shortcuts" start-placeholder="开始日期"
                      end-placeholder="结束日期"
                      :style="{ width: narrow ? '100%' : '320px' }" />
      <el-button type="primary" @click="exportSummary">导出活动汇总</el-button>
      <span class="dim">按活动结束时间筛，含不公开招募（补记录）的活动；留空即不限</span>
    </div>
    <el-row :gutter="16" v-if="d">
      <el-col :span="3" v-for="c in cards" :key="c.label">
        <el-card shadow="never" class="kpi">
          <div class="v" :style="{ color: c.color }">{{ c.value }}</div>
          <div class="dim">{{ c.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top:16px" v-if="d">
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
    <el-row :gutter="16" style="margin-top:16px" v-if="d">
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
            <el-table-column label="时间" width="110">
              <template #default="{row}">{{ fmtDT(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { api, download, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from '../components/DataCards.vue'

const narrow = useNarrow()
const d = ref(null)
const range = ref([])          // 导出的时间跨度，空 = 不限
const trendEl = ref()
const pieEl = ref()

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

// 走 download 而不是 downloadExport：后者先问一次 attachments 有几个附件，
// 这张汇总表没有附件接口，那一问会落空，表也就下不来了
function exportSummary() {
  const [start, end] = range.value || []
  const q = new URLSearchParams()
  if (start) q.set('start', start)
  if (end) q.set('end', end)
  const query = q.toString()
  download(`/api/staff/activities-summary.xlsx${query ? `?${query}` : ''}`)
}

const cards = computed(() => d.value ? [
  { label: '注册学生', value: d.value.cards.students, color: '#1f5aa8' },
  { label: '报名中活动', value: d.value.cards.activities_published, color: '#ae0b2a' },
  { label: '活动总数', value: d.value.cards.activities_all, color: '#606266' },
  { label: '有效报名', value: d.value.cards.active_enrollments, color: '#1e7a52' },
  { label: '候补中', value: d.value.cards.waitlisted, color: '#e6a23c' },
  { label: '24h内签到人次', value: d.value.cards.checked_recent, color: '#7a4bb3' },
  { label: '累计时长(h)', value: d.value.cards.hours_total, color: '#b3661f' },
] : [])

function draw() {
  const axis = { axisLabel: { fontSize: 10 }, grid: { left: 40, right: 16, top: 20, bottom: 26 } }
  echarts.init(trendEl.value).setOption({
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: d.value.enroll_trend.map(x => x.date) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{ type: 'line', smooth: true, areaStyle: { opacity: .12 },
               data: d.value.enroll_trend.map(x => x.count),
               itemStyle: { color: '#ae0b2a' } }],
    ...axis,
  })
  echarts.init(pieEl.value).setOption({
    tooltip: { trigger: 'item' },
    series: [{ type: 'pie', radius: ['38%', '68%'],
      data: d.value.colleges.map(x => ({ name: x.college, value: x.count })),
      label: { fontSize: 10 } }],
  })
}

onMounted(async () => {
  d.value = await api.get('/api/staff/overview')
  await nextTick(draw)
})
</script>

<style scoped>
.kpi { text-align: center; border-radius: 12px; }
.kpi .v { font-size: 26px; font-weight: 700; line-height: 1.3; }
</style>
