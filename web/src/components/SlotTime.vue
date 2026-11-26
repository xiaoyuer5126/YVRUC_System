<!-- 志愿者活动页上一条岗位（或一批统一分配）的时间格：各个时段一行一个，
     下面是这条岗位此刻的报名状态。电脑上的岗位表和手机卡片共用这一份，
     两处对同一条岗位的说法一定一样。

     几个时段的岗位要同一批人全部来，所以标「共 N 个时段 · 需全部参加」；
     同名、不同时段的几条岗位是可以分别报的，那句话写在岗位名那一格。 -->
<template>
  <div class="st">
    <div v-for="x in g.periods" :key="x.id">{{ tTime(x.text) }}</div>
    <div v-if="!g.periods.length">{{ t('待定') }}</div>
    <div v-if="tags.length" class="st-tags">
      <el-tag v-for="x in tags" :key="x.text" size="small" :type="x.type"
              :effect="x.effect || 'plain'">{{ x.text }}</el-tag>
    </div>
    <div v-if="lockText" class="dim">{{ lockText }}</div>
    <div v-if="closedAt" class="dim">{{ t('报名已于 {0} 截止', [fmtDT(g.enroll_deadline)]) }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fmtDT } from '../api'
import { t, tTime } from '../i18n'

const props = defineProps({
  g: { type: Object, required: true },          // 岗位自带的场次，或一批统一分配
  // 活动在报名期、已开始报名：这时某一条岗位不收人了才值得说一句截止时间
  regOpen: { type: Boolean, default: false },
})

const tags = computed(() => {
  const g = props.g
  const out = []
  if (g.periods.length > 1) {
    out.push({ text: t('共 {0} 个时段 · 需全部参加', [g.periods.length]), type: 'warning' })
  }
  if (g.flexible_time) out.push({ text: t('时间灵活·可多次签到'), type: 'success' })
  if (g.enroll_paused) out.push({ text: t('暂停报名'), type: 'danger' })
  if (g.locked) out.push({ text: t('名单已锁定'), type: 'info', effect: 'light' })
  return out
})
/** 「开场前 N 小时锁定名单」：还没锁、会自动锁的给志愿者提个醒 */
const lockText = computed(() => {
  const g = props.g
  if (g.locked || !g.lock_hours) return ''
  return t('开场前 {0} 小时锁定名单', [g.lock_hours])
})
// 每条岗位在自己开场时就停止收人，活动整体可能还在报名期
const closedAt = computed(() => props.regOpen && !props.g.enroll_open)
</script>

<style scoped>
.st { line-height: 1.6; }
.st-tags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
</style>
