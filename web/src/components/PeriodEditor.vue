<!-- 场次的服务时段：可以有好几段，每段精确到分钟。 -->
<template>
  <div class="period-box">
    <div v-for="(p, i) in rows" :key="p.k" class="period-row">
      <DateTimeField v-model="p.start" :default-time="DAY_START"
                     placeholder="开始时间" @update:model-value="emitAll" />
      <span class="sep">至</span>
      <DateTimeField v-model="p.end" :default-time="DAY_END"
                     placeholder="结束时间" @update:model-value="emitAll" />
      <el-button link type="danger" :disabled="rows.length === 1"
                 @click="remove(i)">删除</el-button>
    </div>
    <el-button link type="primary" @click="add">+ 添加时段</el-button>
    <div class="dim tip">
      同一个场次的人在列出的每个时段都要到场。日期不连续、每天时间不同，
      就按段各添加一个；钟点填个大概即可，时长按实际签到签退计算。
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { DAY_END, DAY_START } from '../store'
import DateTimeField from './DateTimeField.vue'

const props = defineProps({
  // [{start_time, end_time}]：与接口里场次的 periods 同一形状
  modelValue: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue'])

let seq = 0
const asDate = v => (v instanceof Date ? v
  : (v ? new Date(String(v).replace(' ', 'T')) : ''))

function normalize(p) {
  return { k: ++seq, start: asDate(p.start_time), end: asDate(p.end_time) }
}

const rows = ref([])
const sig = v => JSON.stringify(v || [])

function iso(v) {
  if (!v) return ''
  const d = v instanceof Date ? v : new Date(v)
  if (Number.isNaN(d.getTime())) return ''
  const z = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`
       + `T${z(d.getHours())}:${z(d.getMinutes())}`
}

function payload() {
  return rows.value.map(p => ({ start_time: iso(p.start), end_time: iso(p.end) }))
}

function emitAll() {
  emit('update:modelValue', payload())
}

// 外面换了内容（打开另一场、智能起草回填）才重建；自己发出去的改动不重来。
// 一行都还没有时不跳过：传进来的是空列表，payload 也是空列表，两边一比
// 「没变」，就不会放那一行空白时段，人得先点「添加时段」才看得到要填哪
watch(() => props.modelValue, v => {
  if (rows.value.length && sig(v) === sig(payload())) return
  const list = Array.isArray(v) && v.length ? v : [{}]
  rows.value = list.map(normalize)
}, { immediate: true, deep: true })

function add() {
  rows.value.push({ k: ++seq, start: '', end: '' })
  emitAll()
}
function remove(i) {
  rows.value.splice(i, 1)
  emitAll()
}
</script>

<style scoped>
.period-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
              margin-bottom: 8px; }
.period-row > :deep(.el-date-editor) { flex: 1 1 180px; min-width: 0; }
.sep { color: var(--el-text-color-secondary); }
.tip { margin-top: 4px; line-height: 1.6; }
</style>
