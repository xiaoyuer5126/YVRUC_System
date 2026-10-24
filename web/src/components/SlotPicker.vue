<!-- 按可参与时段筛选：周视图，一格两小时（8–22 点），点格子多选，翻周时已选的照留。
     值是「YYYY-MM-DDTHH」的列表，原样交给广场列表接口的 slots。 -->
<template>
  <div class="slot-box">
    <div class="slot-nav">
      <el-button size="small" :disabled="week === 0" @click="week--">‹ 上一周</el-button>
      <b>{{ rangeText }}</b>
      <el-button size="small" :disabled="week >= MAX_WEEK" @click="week++">下一周 ›</el-button>
    </div>
    <div class="slot-grid">
      <div />
      <div v-for="d in days" :key="d.key" class="slot-day">
        <div>周{{ d.wd }}</div>
        <div class="dim">{{ d.md }}</div>
      </div>
      <template v-for="h in HOURS" :key="h">
        <div class="slot-hour dim">{{ h }}–{{ h + 2 }}</div>
        <button v-for="d in days" :key="d.key + h" type="button" class="slot-cell"
                :class="{ on: picked.has(cellKey(d, h)) }" :disabled="past(d, h)"
                :aria-label="`周${d.wd} ${h} 点到 ${h + 2} 点`"
                @click="toggle(cellKey(d, h))" />
      </template>
    </div>
    <div class="slot-foot dim">
      <span>已选 {{ modelValue.length }} 格<template v-if="modelValue.length">
        · <el-link type="primary" underline="never" @click="emit('update:modelValue', [])">清空</el-link>
      </template></span>
      <span>只看报名中的活动；场次有一段落在所选时段里就列出来</span>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({ modelValue: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])

const HOURS = [8, 10, 12, 14, 16, 18, 20]
const WD = ['日', '一', '二', '三', '四', '五', '六']
// 往后能翻几周：后端一次最多收五周的格子
const MAX_WEEK = 4
const z = n => String(n).padStart(2, '0')
const ymd = d => `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`

const week = ref(0)
// 从本周一数起，翻到第几周就往后挪几个七天
const days = computed(() => Array.from({ length: 7 }, (_, i) => {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7) + week.value * 7 + i)
  return { date: d, key: ymd(d), wd: WD[d.getDay()], md: `${z(d.getMonth() + 1)}-${z(d.getDate())}` }
}))
const rangeText = computed(() => `${days.value[0].md}～${days.value[6].md}`)
const picked = computed(() => new Set(props.modelValue))

const cellKey = (d, h) => `${d.key}T${z(h)}`
function past(d, h) {
  const end = new Date(d.date)
  end.setHours(h + 2)
  return end <= new Date()
}
function toggle(k) {
  const s = new Set(props.modelValue)
  if (s.has(k)) s.delete(k)
  else s.add(k)
  emit('update:modelValue', [...s].sort())
}
</script>

<style scoped>
.slot-box { border: 1px solid #eceef1; border-radius: 8px; padding: 12px; margin-bottom: 16px; }
.slot-nav { display: flex; align-items: center; justify-content: space-between; gap: 8px;
            margin-bottom: 8px; }
.slot-grid { display: grid; grid-template-columns: 44px repeat(7, minmax(0, 1fr)); gap: 4px;
             max-width: 640px; margin: 0 auto; }
.slot-day { text-align: center; font-size: 12px; line-height: 1.4; }
.slot-hour { font-size: 12px; display: flex; align-items: center; }
.slot-cell { height: 28px; border: 1px solid #dcdfe6; border-radius: 4px; background: #fff;
             cursor: pointer; padding: 0; }
.slot-cell.on { background: var(--el-color-primary); border-color: var(--el-color-primary); }
.slot-cell:disabled { background: #f5f7fa; cursor: not-allowed; }
.slot-foot { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap;
             margin-top: 8px; font-size: 12px; }
</style>
