<template>
  <span class="refresh-bar">
    <el-button :size="size" type="primary" :loading="busy" @click="fire">
      <el-icon><Refresh /></el-icon>{{ t('&nbsp;刷新') }}</el-button>
    <span class="dim">{{ busy ? t('正在刷新…') : (at ? t('更新于 {0}', [timeOf(at)]) : t('尚未加载')) }}</span>
  </span>
</template>

<script setup>
/**
 * 手动刷新按钮 + 这份数据的更新时刻。
 *
 * 现场数据（到岗、服装、签收、剩余名额）只在点这里时才重新拉。自动轮询会让页面
 * 在人眼皮底下无声跳动：正在数人头，数字变了；勾好了几个人准备批量操作，
 * 列表刷掉了。既然不自动刷，就得把「这份数据是什么时候的」写在旁边，
 * 否则看的人无从判断屏幕上是不是老数据。用实心主色：它是这一屏上
 * 最常点的按钮，找不到它的人只会以为数字不准。
 *
 * 时刻由数据的主人给（at）：在自己的加载函数里拉成功就记一笔。首次进页面、
 * 点这里刷新、操作之后自动重拉，走的都是那个加载函数，时刻自然都对；
 * 要是由这里自己记，只有点按钮那一路记得上，别的路拉来的新数据旁边
 * 还挂着旧时刻。
 */
import { ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { t } from '../i18n'

const props = defineProps({
  // 返回 Promise 的加载函数；组件只负责转圈
  load: { type: Function, required: true },
  // 这份数据上次拉成功的时刻（Date.now() 的毫秒数），还没拉到为空
  at: { type: Number, default: 0 },
  // 跟着所在那一排走：工具栏里用默认尺寸，区块标题旁用 small
  size: { type: String, default: 'default' },
})

const busy = ref(false)
const timeOf = ms => new Date(ms).toLocaleTimeString('zh-CN', { hour12: false })

async function fire() {
  if (busy.value) return
  busy.value = true
  try {
    await props.load()
  } finally {
    busy.value = false
  }
}
defineExpose({ fire })
</script>

<style scoped>
.refresh-bar { display: inline-flex; align-items: center; gap: 8px; }
</style>
