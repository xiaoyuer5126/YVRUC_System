<template>
  <div class="page page-wide">
    <h2>数据总览</h2>
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
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import DataCards from '../components/DataCards.vue'

const narrow = useNarrow()
const d = ref(null)
const trendEl = ref()
const pieEl = ref()

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
