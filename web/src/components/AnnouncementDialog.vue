<!-- 平台公告弹窗。正文按纯文本显示：保留换行，网址拆出来做成链接，
     不把正文当 HTML 渲染（公告是管理员写的，但 HTML 一开口子就不止这一处）。 -->
<template>
  <el-dialog :model-value="modelValue" :title="title" :width="narrow ? '94%' : '520px'"
             append-to-body @update:model-value="v => emit('update:modelValue', v)">
    <div class="body">
      <template v-for="(part, i) in parts" :key="i">
        <a v-if="part.url" :href="part.url" target="_blank" rel="noopener noreferrer">{{ part.text }}</a>
        <template v-else>{{ part.text }}</template>
      </template>
    </div>
    <template #footer>
      <el-button type="primary" @click="emit('update:modelValue', false)">知道了</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed } from 'vue'
import { useNarrow } from '../composables/useNarrow'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, default: '' },
  body: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])
const narrow = useNarrow()

// 只认 http(s) 开头的网址；句末的中文标点不算进链接
const URL_RE = /https?:\/\/[^\s，。；！？、）》」]+/g

const parts = computed(() => {
  const out = []
  let last = 0
  for (const m of props.body.matchAll(URL_RE)) {
    if (m.index > last) out.push({ text: props.body.slice(last, m.index) })
    out.push({ text: m[0], url: m[0] })
    last = m.index + m[0].length
  }
  if (last < props.body.length) out.push({ text: props.body.slice(last) })
  return out
})
</script>

<style scoped>
.body { white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
</style>
