<template>
  <div class="page">
    <div class="hero">
      <h2>活动广场</h2>
      <p>即选即得——报名即生效；报名后筛选——由负责人筛选录取</p>
    </div>

    <div class="filters">
      <el-input v-model="f.q" placeholder="搜索活动名称" clearable
                style="width:220px" @input="onSearch" @clear="reload" />
      <el-select v-model="f.status" style="width:140px" @change="reload">
        <el-option v-for="s in STATUS_OPTIONS" :key="s.value"
                   :value="s.value" :label="s.label" />
      </el-select>
      <el-select v-model="f.mode" placeholder="活动类型" clearable
                 style="width:150px" @change="reload">
        <el-option value="instant" label="即选即得" />
        <el-option value="screened" label="报名后筛选" />
      </el-select>
      <el-select v-model="f.organizer" placeholder="组织单位" clearable filterable
                 style="width:220px" @change="reload">
        <el-option v-for="o in organizers" :key="o" :value="o" :label="o" />
      </el-select>
    </div>

    <div v-if="list.length" class="card-grid">
      <div v-for="a in list" :key="a.id" class="act-card square-card"
           @click="$router.push('/activity/' + a.id)">
        <div class="card-top">
          <el-tag size="small" effect="dark" :color="modeColor(a.recruit_mode)" style="border:none">
            {{ a.recruit_label }}
          </el-tag>
          <el-tag size="small" :type="statusType(a.display_status)">{{ a.display_label }}</el-tag>
        </div>
        <div class="card-title">
          <el-tag v-if="a.urgent" size="small" type="danger" effect="dark" class="urgent">急招</el-tag>
          {{ a.title }}
        </div>
        <div v-if="a.organizer" class="meta-row"><el-icon><OfficeBuilding /></el-icon>{{ a.organizer }}</div>
        <div class="meta-row"><el-icon><Clock /></el-icon>{{ a.time_text }}</div>
        <div class="meta-row"><el-icon><Location /></el-icon>{{ a.location || '待定' }}</div>
        <div class="meta-row">
          <!-- 还没开始报名：这张卡上最要紧的信息是「还有多久」，不是剩几个名额 -->
          <span v-if="a.opensAt" class="countdown-row">
            距报名开始
            <el-countdown :value="a.opensAt" :format="fmt(a)" @finish="unlock(a)" />
          </span>
          <span v-else-if="a.status === 'published'">
            剩余 <b style="color:var(--el-color-primary)">{{ a.remaining }}</b> / {{ a.total_capacity }}
          </span>
          <span v-else class="dim">已报名 {{ a.total_enrolled }} 人</span>
        </div>
      </div>
    </div>
    <el-empty v-else description="暂无活动" />

    <el-pagination v-if="total > f.size" class="pager" layout="prev, pager, next"
                   background :current-page="f.page" :page-size="f.size"
                   :total="total" @current-change="goPage" />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { MODE_COLORS } from '../store'

const route = useRoute()
const router = useRouter()
const STATUS_OPTIONS = [
  { value: 'published', label: '报名中' },
  { value: 'upcoming', label: '未开始报名' },
  { value: 'closed', label: '已截止' },
  { value: 'ongoing', label: '进行中' },
  { value: 'finished', label: '已结束' },
  { value: '', label: '全部状态' },
]
// 筛选条件同步到地址栏：点进活动再返回，筛到第几页还在
const f = reactive({
  q: route.query.q || '',
  status: route.query.status ?? 'published',
  mode: route.query.mode || '',
  organizer: route.query.organizer || '',
  page: Number(route.query.page) || 1,
  size: 12,
})
const list = ref([])
const organizers = ref([])
const total = ref(0)
const modeColor = m => MODE_COLORS[m]?.color

// 展示状态 → el-tag type 颜色
// 展示状态 → el-tag 颜色。以后端 display_status 为准，别在这里另算一套
const STATUS_TYPES = {
  upcoming: 'primary',    // 未开始报名
  published: 'success',   // 报名中
  closed: 'warning',      // 已截止
  ongoing: 'danger',      // 进行中
  finished: 'info',       // 已结束
}
const statusType = code => STATUS_TYPES[code] || 'info'

/** 倒计时目标：后端给的「还剩多少毫秒」加上收到响应的此刻，在 load 里算一次。
 *
 * 不直接 parse reg_start——客户端时钟偏一点，倒计时就跟服务端对不上。
 * 也不能写在模板里现算：每重绘一次目标就往后挪一截。
 */
const fmt = a => (a.opens_in > 86400 ? 'DD 天 HH:mm:ss' : 'HH:mm:ss')

/** 倒计时归零：卡片就地翻成「报名中」，不等重拉（道理同活动详情页的 unlock）。
 * 列表随后错开 0–3 秒再拉，几百人别在同一毫秒一起刷。 */
let refreshTimer = null
function unlock(a) {
  a.opens_in = 0
  a.opens_in_ms = 0
  a.opensAt = 0
  if (a.display_status === 'upcoming') {
    a.display_status = 'published'
    a.display_label = '报名中'
  }
  clearTimeout(refreshTimer)
  refreshTimer = setTimeout(load, Math.random() * 3000)
}
onBeforeUnmount(() => clearTimeout(refreshTimer))

async function load() {
  const query = new URLSearchParams({
    q: f.q, status: f.status, mode: f.mode, organizer: f.organizer,
    page: f.page, size: f.size,
  })
  const d = await api.get(`/api/activities?${query}`)
  const at = Date.now()
  for (const a of d.items) {
    const ms = a.opens_in_ms ?? a.opens_in * 1000
    a.opensAt = ms ? at + ms : 0
  }
  list.value = d.items
  total.value = d.total
  organizers.value = d.organizers
  router.replace({ query: {
    ...(f.q ? { q: f.q } : {}), ...(f.mode ? { mode: f.mode } : {}),
    ...(f.organizer ? { organizer: f.organizer } : {}),
    ...(f.status === 'published' ? {} : { status: f.status }),
    ...(f.page > 1 ? { page: f.page } : {}),
  } })
}

// 换筛选条件就回第一页：留在第 5 页多半是空的
function reload() {
  f.page = 1
  load()
}
function goPage(p) {
  f.page = p
  load()
}
let timer = null
function onSearch() {
  clearTimeout(timer)
  timer = setTimeout(reload, 300)
}
onMounted(load)
</script>

<style scoped>
.hero { margin-bottom:12px; }
.hero h2 { margin:0; }
.filters { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:16px; }
.pager { margin-top:16px; justify-content:center; }
.hero p { margin:8px 0 0; color:#909399; font-size:13px; line-height:1.7; }
.square-card { padding:16px 18px; }
.card-top { display:flex; justify-content:space-between; align-items:center; gap:8px; }
.card-title {
  font-size:16px; font-weight:600; color:#1f2328; line-height:1.4;
  margin:12px 0 4px;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;
}
.urgent { margin-right:8px; vertical-align:2px; font-weight:600; }
.countdown-row { display:inline-flex; align-items:center; gap:8px; }
.countdown-row :deep(.el-statistic__content) {
  font-size:14px; font-weight:600; color:var(--el-color-primary);
}
</style>
