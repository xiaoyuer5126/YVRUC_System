<template>
  <div class="page">
    <div class="hero">
      <h2>{{ t('活动广场') }}</h2>
      <p>{{ t('即选即得——报名即生效；报名后筛选——由负责人筛选录取') }}</p>
    </div>

    <div class="filters">
      <el-input v-model="f.q" :placeholder="t('搜索活动名称')" clearable
                style="width:220px" @input="onSearch" @clear="reload" />
      <!-- 筛选都用「全部」那一项（空串）回到不筛，不带清除按钮：清除会把值置成
           undefined，拼进查询串就成了 mode=undefined。empty-values 让空串照常
           显示成「全部…」，而不是被当作没选、显示占位文字 -->
      <!-- 按时段筛选时只看报名中的，状态下拉暂时用不上 -->
      <el-select v-model="f.status" :empty-values="[null, undefined]" :disabled="slots.length > 0"
                 style="width:140px" @change="reload">
        <el-option v-for="s in statusOptions" :key="s.value"
                   :value="s.value" :label="s.label" />
      </el-select>
      <el-select v-model="f.mode" :empty-values="[null, undefined]"
                 style="width:150px" @change="reload">
        <el-option value="" :label="t('全部类型')" />
        <el-option value="instant" :label="t('即选即得')" />
        <el-option value="screened" :label="t('报名后筛选')" />
      </el-select>
      <el-select v-model="f.organizer" :empty-values="[null, undefined]" filterable
                 style="width:220px" @change="reload">
        <el-option value="" :label="t('全部组织单位')" />
        <el-option v-for="o in organizers" :key="o" :value="o" :label="o" />
      </el-select>
      <!-- 日期范围与可参与时段都收进浮窗：电脑点按钮弹出，手机从底部抽屉展开 -->
      <el-popover v-if="!narrow" v-model:visible="slotOpen" trigger="click"
                  placement="bottom-end" :width="620">
        <template #reference>
          <el-button class="slot-trigger" :type="slotActive ? 'primary' : ''" plain>
            {{ slotButtonText }}
          </el-button>
        </template>
        <SlotPicker v-model:start="f.start" v-model:end="f.end" v-model:slots="slots" />
      </el-popover>
      <el-button v-else class="slot-trigger" :type="slotActive ? 'primary' : ''" plain
                 @click="slotOpen = true">{{ slotButtonText }}</el-button>
      <el-drawer v-if="narrow" v-model="slotOpen" direction="btt" size="85%"
                 :title="t('按时段筛选')" append-to-body>
        <SlotPicker v-model:start="f.start" v-model:end="f.end" v-model:slots="slots" />
        <div class="slot-apply">
          <el-button type="primary" class="slot-apply-btn" @click="slotOpen = false">
            {{ slotApplyText }}
          </el-button>
        </div>
      </el-drawer>
      <el-button v-if="filtered" text type="primary" @click="clearFilters">{{ t('清除筛选') }}</el-button>
    </div>

    <div v-if="list.length" class="card-grid">
      <div v-for="a in list" :key="a.id" class="act-card square-card"
           @click="$router.push('/activity/' + a.id)">
        <div class="card-top">
          <el-tag size="small" effect="dark" :color="modeColor(a.recruit_mode)" style="border:none">
            {{ t(a.recruit_label) }}
          </el-tag>
          <el-tag size="small" :type="statusType(a.display_status)">{{ t(a.display_label) }}</el-tag>
        </div>
        <div class="card-title">
          <el-tag v-if="a.urgent" size="small" type="danger" effect="dark" class="urgent">{{ t('急招') }}</el-tag>
          {{ a.title }}
        </div>
        <div v-if="a.organizer_text" class="meta-row"><el-icon><OfficeBuilding /></el-icon>{{ a.organizer_text }}</div>
        <div class="meta-row"><el-icon><Clock /></el-icon>{{ tTime(a.time_text) }}</div>
        <div class="meta-row"><el-icon><Location /></el-icon>{{ a.location || t('待定') }}</div>
        <!-- 按时段筛出来的：要来好几段、或者钟点只是个大概的，卡片上先说一声 -->
        <div v-if="a.slot_note && (a.slot_note.periods > 1 || a.slot_note.flexible)" class="meta-row">
          <el-tag v-if="a.slot_note.periods > 1" size="small" type="warning" effect="plain">{{ t('场次共 {0} 段，需每段到场', [a.slot_note.periods]) }}</el-tag>
          <el-tag v-if="a.slot_note.flexible" size="small" type="success" effect="plain">{{ t('时间灵活') }}</el-tag>
        </div>
        <div class="meta-row">
          <!-- 还没开始报名：这张卡上最要紧的信息是「还有多久」，不是剩几个名额 -->
          <span v-if="a.opensAt" class="countdown-row">{{ t('距报名开始') }}<el-countdown :value="a.opensAt" :format="fmt(a)" @finish="unlock(a)" />
          </span>
          <span v-else-if="a.status === 'published'">{{ t('剩余') }}<b style="color:var(--el-color-primary)">{{ a.remaining }}</b> / {{ a.total_capacity }}
          </span>
          <span v-else class="dim">{{ t('已报名 {0} 人', [a.total_enrolled]) }}</span>
        </div>
      </div>
    </div>
    <el-empty v-else :description="emptyText" />

    <el-pagination v-if="total > f.size" class="pager" layout="prev, pager, next"
                   background :current-page="f.page" :page-size="f.size"
                   :total="total" @current-change="goPage" />
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, fmtDTShort } from '../api'
import SlotPicker from '../components/SlotPicker.vue'
import { MODE_COLORS } from '../store'
import { useNarrow } from '../composables/useNarrow'
import { t, tTime } from '../i18n'

const route = useRoute()
const router = useRouter()
const narrow = useNarrow()
const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'published', label: '报名中' },
  { value: 'upcoming', label: '未开始报名' },
  { value: 'closed', label: '已截止' },
  { value: 'ongoing', label: '进行中' },
  { value: 'finished', label: '已结束' },
]
// 选项文字跟着语言走：切语言时下拉里的字也得跟着换
const statusOptions = computed(() => STATUS_OPTIONS.map(o => ({ ...o, label: t(o.label) })))
const emptyText = computed(() => (slots.value.length
  ? t('所选时段里没有报名中的活动，换几格试试')
  : (f.start || f.end ? t('这个时间范围内没有活动，换个范围试试') : t('暂无活动'))))
const slotButtonText = computed(() => {
  let text = t('按时段筛选')
  if (f.start && f.end) text += ` · ${fmtDTShort(f.start)}～${fmtDTShort(f.end)}`
  if (slots.value.length) text += t('（{0} 格）', [slots.value.length])
  return text
})
const slotApplyText = computed(() => {
  if (slots.value.length) return t('看所选时段的活动')
  return f.start || f.end ? t('看这几天的活动') : t('看全部活动')
})
// 筛选条件同步到地址栏：点进活动再返回，筛到第几页还在
const f = reactive({
  q: route.query.q || '',
  status: route.query.status || '',
  mode: route.query.mode || '',
  organizer: route.query.organizer || '',
  start: route.query.start || '',
  end: route.query.end || '',
  page: Number(route.query.page) || 1,
  size: 12,
})

// 按可参与时段筛选选中的格子：不进地址栏，离开广场就不记了
const slotOpen = ref(false)
const slots = ref([])
const slotActive = computed(() => Boolean(f.start || f.end) || slots.value.length > 0)
let slotTimer = null
watch([slots, () => f.start, () => f.end], () => {
  clearTimeout(slotTimer)
  slotTimer = setTimeout(reload, 400)     // 连点几格、拖日期只拉一次
})

// 搜索词、三个下拉、日期和时段格子任一有值，就摆出「清除筛选」
const filtered = computed(() => Boolean(f.q || f.status || f.mode || f.organizer) || slotActive.value)
function clearFilters() {
  Object.assign(f, { q: '', status: '', mode: '', organizer: '', start: '', end: '' })
  slots.value = []
  reload()
  // 上面改日期和格子会触发防抖重拉，这里已经拉过了
  nextTick(() => clearTimeout(slotTimer))
}

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
const fmt = a => (a.opens_in > 86400 ? t('DD 天 HH:mm:ss') : 'HH:mm:ss')

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
onBeforeUnmount(() => { clearTimeout(refreshTimer); clearTimeout(slotTimer) })

async function load() {
  const query = new URLSearchParams({
    q: f.q, status: f.status, mode: f.mode, organizer: f.organizer,
    start: f.start, end: f.end, page: f.page, size: f.size,
  })
  if (slots.value.length) query.set('slots', slots.value.join(','))
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
    ...(f.start ? { start: f.start } : {}), ...(f.end ? { end: f.end } : {}),
    ...(f.status ? { status: f.status } : {}),
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
.slot-apply { margin-top:16px; }
.slot-apply-btn { width:100%; }
.hero p { margin:8px 0 0; color:#909399; font-size:13px; line-height: 1.6; }
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
