<template>
  <span class="refresh-bar">
    <el-button size="small" type="primary" :loading="busy" @click="fire">
      <el-icon><Refresh /></el-icon>&nbsp;刷新
    </el-button>
    <span class="dim">{{ busy ? '正在刷新…' : (at ? `更新于 ${at}` : '尚未加载') }}</span>
  </span>
</template>

<script setup>
/**
 * 手动刷新按钮 + 上次更新时刻。
 *
 * 现场数据（到岗、服装、签收、剩余名额）只在点这里时才重新拉。自动轮询会让页面
 * 在人眼皮底下无声跳动：正在数人头，数字变了；勾好了几个人准备批量操作，
 * 列表刷掉了。既然不自动刷，就得把「这份数据是什么时候的」写在旁边，
 * 否则看的人无从判断屏幕上是不是老数据。用实心主色：它是这一屏上
 * 最常点的按钮，找不到它的人只会以为数字不准。
 */
import { ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'

const props = defineProps({
  // 返回 Promise 的加载函数；组件负责转圈与记录时刻
  load: { type: Function, required: true },
})

const busy = ref(false)
const at = ref('')

async function fire() {
  if (busy.value) return
  busy.value = true
  try {
    await props.load()
    at.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } finally {
    busy.value = false
  }
}

// 首次由父级 onMounted 触发，时刻也要记上
function markLoaded() {
  at.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
}
defineExpose({ fire, markLoaded })
</script>

<style scoped>
.refresh-bar { display: inline-flex; align-items: center; gap: 8px; }
</style>
