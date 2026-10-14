<!-- 一条留痕的变更明细：「字段：旧 → 新」，一行一条。
     名单导入、确认全部这类操作按人记，一条留痕可能带几百行明细：先列前几行，
     其余点开再看，不然一条记录就把整页撑满。old 为 null 的是说明（如跳过的行），
     只有「标签：内容」。 -->
<template>
  <div>
    <div v-for="(ch, i) in shown" :key="i" class="dim" :style="lineStyle">
      {{ ch.label }}：<template v-if="ch.old !== null">{{ ch.old || '空' }} → </template>
      <b v-if="strong" style="color:var(--el-color-primary)">{{ ch.new || '空' }}</b>
      <template v-else>{{ ch.new || '空' }}</template>
    </div>
    <el-button v-if="changes.length > LIMIT" link type="primary" size="small"
               @click="open = !open">
      {{ open ? '收起' : `展开其余 ${changes.length - LIMIT} 条` }}
    </el-button>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  changes: { type: Array, default: () => [] },
  // 新值加粗高亮（卡片和时间线里用；表格单元格里不加）
  strong: { type: Boolean, default: false },
  lineStyle: { type: String, default: '' },
})
const LIMIT = 5
const open = ref(false)
const shown = computed(() => (open.value ? props.changes : props.changes.slice(0, LIMIT)))
</script>
