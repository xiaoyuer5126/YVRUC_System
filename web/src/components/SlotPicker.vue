<!-- 广场「按时段筛选」浮窗：日期范围 + 可参与时段网格。
     日期走 v-model:start / v-model:end（'YYYY-MM-DD'，空串 = 不限），
     格子走 v-model:slots，值是「YYYY-MM-DDTHH」的列表，原样交给广场列表接口的 slots。
     一格两小时（8–22 点）：点日期表头切换一整天，点左侧时段切换这一页的一行；
     日期跨度超过 7 天时按周翻页。 -->
<template>
  <div class="slot-box">
    <div class="slot-sec">日期范围</div>
    <!-- 起止各用一个单月面板：双月的范围面板比浮窗和手机屏都宽。
         teleported 关掉：日期面板留在浮窗里，点面板不会被当成点了浮窗外 -->
    <div class="slot-range">
      <el-date-picker :model-value="start || null" type="date" value-format="YYYY-MM-DD"
                      placeholder="开始日期" :teleported="false" @update:model-value="setStart" />
      <span class="dim">至</span>
      <el-date-picker :model-value="end || null" type="date" value-format="YYYY-MM-DD"
                      placeholder="结束日期" :teleported="false"
                      :disabled-date="d => !!start && ymd(d) < start" @update:model-value="setEnd" />
    </div>
    <div class="slot-chips">
      <el-check-tag v-for="(s, i) in SHORTCUTS" :key="s.text"
                    :checked="activeShortcut === i" @change="applyShortcut(i)">
        {{ s.text }}
      </el-check-tag>
    </div>

    <div class="slot-divider" />
    <div class="slot-sec">可参与时段（不选格子就只按日期筛）</div>

    <div v-if="pages > 1" class="slot-nav">
      <el-button size="small" :disabled="page === 0" @click="page--">‹ 上一周</el-button>
      <b>{{ pageRangeText }}</b>
      <el-button size="small" :disabled="page >= pages - 1" @click="page++">下一周 ›</el-button>
    </div>

    <div class="slot-grid" :style="gridStyle">
      <div />
      <button v-for="d in shownDays" :key="d.key" type="button" class="slot-day"
              :class="{ full: dayFull(d) }" :aria-label="`${d.md} 周${d.wd}，全选或清空这一天`"
              @click="toggleDay(d)">
        <span>周{{ d.wd }}</span>
        <span class="dim">{{ d.md }}</span>
      </button>
      <template v-for="h in HOURS" :key="h">
        <button type="button" class="slot-hour"
                :aria-label="`${h} 点到 ${h + 2} 点，全选或清空这一行`"
                @click="toggleRow(h)">{{ h }}–{{ h + 2 }}</button>
        <button v-for="d in shownDays" :key="d.key + h" type="button" class="slot-cell"
                :class="{ on: picked.has(cellKey(d, h)) }" :disabled="past(d, h)"
                :aria-label="`周${d.wd} ${h} 点到 ${h + 2} 点`"
                @click="toggleCell(cellKey(d, h))" />
      </template>
    </div>

    <div class="slot-foot">
      <span>已选 {{ slots.length }} 格<template v-if="slots.length">
        · <el-link type="primary" underline="never" @click="clearSlots">清空</el-link>
      </template></span>
      <span class="dim">{{ slots.length
        ? '只看报名中的活动；场次有一段落在所选时段里就列出来'
        : '只按日期范围筛，状态可以照常选' }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  start: { type: String, default: '' },
  end: { type: String, default: '' },
  slots: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:start', 'update:end', 'update:slots'])

const HOURS = [8, 10, 12, 14, 16, 18, 20]
const WD = ['日', '一', '二', '三', '四', '五', '六']
// 一次最多能选的格子数，与后端 backend/routers/activities.py 的 SLOT_MAX 一致
const SLOT_MAX = 245
const PAGE = 7

const z = n => String(n).padStart(2, '0')
const ymd = d => `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`
const mdOf = d => `${z(d.getMonth() + 1)}-${z(d.getDate())}`
const today = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d }
const plusDays = n => { const d = today(); d.setDate(d.getDate() + n); return d }
// 本周末按「最近的周六到周日」算：今天就是周六时，周末是今明两天
function thisWeekend() {
  const sat = plusDays((6 - new Date().getDay() + 7) % 7)
  const sun = new Date(sat)
  sun.setDate(sat.getDate() + 1)
  return [sat, sun]
}
const SHORTCUTS = [
  { text: '今天', value: () => [today(), today()] },
  { text: '本周末', value: thisWeekend },
  { text: '未来 7 天', value: () => [today(), plusDays(6)] },
  { text: '未来 30 天', value: () => [today(), plusDays(29)] },
]

// 起止要么都有、要么都空：只填一头时另一头先取同一天，清掉任一头就是不限
function setRange(a, b) {
  emit('update:start', a)
  emit('update:end', b)
}
const setStart = v => (v ? setRange(v, props.end && props.end >= v ? props.end : v) : setRange('', ''))
const setEnd = v => (v ? setRange(props.start && props.start <= v ? props.start : v, v) : setRange('', ''))
const activeShortcut = computed(() => {
  if (!props.start || !props.end) return -1
  return SHORTCUTS.findIndex(s => {
    const [a, b] = s.value()
    return ymd(a) === props.start && ymd(b) === props.end
  })
})
function applyShortcut(i) {
  const [a, b] = SHORTCUTS[i].value()
  setRange(ymd(a), ymd(b))
}

// 要显示的日子：日期范围内每一天；没选范围时从今天起 7 天
const days = computed(() => {   // vuecheck: script-only，模板经 shownDays 读它
  const out = []
  if (props.start && props.end) {
    const d = new Date(props.start + 'T00:00')
    const end = new Date(props.end + 'T00:00')
    while (d <= end) {
      out.push(new Date(d))
      d.setDate(d.getDate() + 1)
    }
  } else {
    for (let i = 0; i < PAGE; i++) out.push(plusDays(i))
  }
  return out.map(d => ({ date: d, key: ymd(d), wd: WD[d.getDay()], md: mdOf(d) }))
})
const pages = computed(() => Math.max(1, Math.ceil(days.value.length / PAGE)))
const page = ref(0)
const shownDays = computed(() => days.value.slice(page.value * PAGE, page.value * PAGE + PAGE))
const pageRangeText = computed(() => {
  const list = shownDays.value
  return list.length ? `${list[0].md}～${list[list.length - 1].md}` : ''
})
const gridStyle = computed(() => ({
  gridTemplateColumns: `44px repeat(${Math.max(1, shownDays.value.length)}, minmax(0, 1fr))`,
}))

const picked = computed(() => new Set(props.slots))
const cellKey = (d, h) => `${d.key}T${z(h)}`
// 一格结束（开始两小时后）就算过去了
function past(d, h) {
  const end = new Date(d.date)
  end.setHours(h + 2, 0, 0, 0)
  return end <= new Date()
}

// 日期范围一变就回第一页，并把范围外的格子去掉
watch([() => props.start, () => props.end], () => {
  page.value = 0
  if (!props.start || !props.end) return
  const kept = props.slots.filter(k => k.slice(0, 10) >= props.start && k.slice(0, 10) <= props.end)
  if (kept.length !== props.slots.length) emit('update:slots', kept.sort())
})

function emitSlots(s) { emit('update:slots', [...s].sort()) }
function clearSlots() { emit('update:slots', []) }

// 表头、时段标签、单格共用：没全选就全选，已全选就全清；一次最多 SLOT_MAX 格
function toggleKeys(keys) {
  if (!keys.length) return
  const s = new Set(props.slots)
  if (keys.every(k => s.has(k))) {
    keys.forEach(k => s.delete(k))
    emitSlots(s)
    return
  }
  const add = keys.filter(k => !s.has(k))
  const room = SLOT_MAX - s.size
  if (add.length > room) {
    add.slice(0, Math.max(room, 0)).forEach(k => s.add(k))
    ElMessage.warning(`一次最多选 ${SLOT_MAX} 格`)
  } else {
    add.forEach(k => s.add(k))
  }
  emitSlots(s)
}
const liveKeys = d => HOURS.filter(h => !past(d, h)).map(h => cellKey(d, h))
const dayFull = d => {
  const keys = liveKeys(d)
  return keys.length > 0 && keys.every(k => picked.value.has(k))
}
const toggleDay = d => toggleKeys(liveKeys(d))
const toggleRow = h => toggleKeys(shownDays.value.filter(d => !past(d, h)).map(d => cellKey(d, h)))
const toggleCell = k => toggleKeys([k])
</script>

<style scoped>
.slot-box { padding: 12px; }
.slot-sec { font-size: 12px; color: #909399; margin-bottom: 8px; }
.slot-range { display: flex; align-items: center; gap: 8px; }
.slot-range :deep(.el-date-editor) { flex: 1; min-width: 0; }
.slot-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.slot-divider { border-top: 1px solid #eceef1; margin: 12px 0; }
.slot-nav { display: flex; align-items: center; justify-content: space-between; gap: 8px;
            margin-bottom: 8px; }
.slot-grid { display: grid; gap: 4px; max-width: 640px; margin: 0 auto; }
.slot-day { text-align: center; font-size: 12px; line-height: 1.4; color: #303133;
            border: none; background: none; padding: 2px 0; font-family: inherit;
            cursor: pointer; border-radius: 4px; }
.slot-day span { display: block; }
.slot-day:hover { background: #ecf5ff; }
.slot-day.full, .slot-day.full .dim { color: var(--el-color-primary); font-weight: 600; }
.slot-hour { font-size: 12px; display: flex; align-items: center; color: #909399;
             border: none; background: none; padding: 0; font-family: inherit;
             text-align: left; cursor: pointer; border-radius: 4px; }
.slot-hour:hover { background: #ecf5ff; color: var(--el-color-primary); }
.slot-cell { height: 28px; border: 1px solid #dcdfe6; border-radius: 4px; background: #fff;
             cursor: pointer; padding: 0; }
.slot-cell.on { background: var(--el-color-primary); border-color: var(--el-color-primary); }
.slot-cell:disabled { background: #f5f7fa; cursor: not-allowed; }
.slot-foot { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap;
             margin-top: 8px; font-size: 12px; }
</style>
