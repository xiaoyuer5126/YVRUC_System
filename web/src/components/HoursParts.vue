<!-- 一笔时长的明细：由哪几段组成（场次 · 时段 · 岗位 · 小时）。 -->
<template>
  <div v-if="parts?.length" class="parts">
    <div v-for="(p, i) in parts" :key="i" class="part">
      <span>{{ p.label }}</span>
      <span v-if="p.time" class="dim">{{ p.time }}</span>
      <span v-if="p.position" class="dim">{{ p.position }}</span>
      <b>{{ p.hours }}h</b>
    </div>
  </div>
  <span v-else class="dim">没有明细</span>
</template>

<script setup>
defineProps({ parts: { type: Array, default: () => [] } })
</script>

<style scoped>
.parts { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.part { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; line-height: 1.7; }
.part b { color: var(--el-color-primary); }
</style>
