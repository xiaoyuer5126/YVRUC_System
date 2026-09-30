<!-- 一笔时长的明细：由哪几段组成（场次 · 时段 · 岗位 · 小时），
     外加管理者手动补录的那几段（kind="manual"，标签就是备注）。 -->
<template>
  <div v-if="parts?.length" class="parts">
    <div v-for="(p, i) in parts" :key="i" class="part">
      <span>{{ p.label || (p.kind === 'import' ? '名单导入' : '') }}</span>
      <span v-if="p.time" class="dim">{{ p.time }}</span>
      <span v-if="p.position" class="dim">{{ p.position }}</span>
      <b>{{ p.hours }}h</b>
      <!-- 只有管理者能删，而且只删得掉手动加的那几段：签到算出来的那些段是
           记录的依据，删了账就没了出处 -->
      <el-button v-if="removable && p.kind === 'manual'" size="small" link
                 type="danger" @click="$emit('remove', p)">删除</el-button>
    </div>
  </div>
  <span v-else class="dim">没有明细</span>
</template>

<script setup>
defineProps({
  parts: { type: Array, default: () => [] },
  removable: { type: Boolean, default: false },
})
defineEmits(['remove'])
</script>

<style scoped>
.parts { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.part { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; line-height: 1.7; }
.part b { color: var(--el-color-primary); }
</style>
