<!-- 一个岗位的报名动作：选中 / 候补 / 退选 / 确认转正，以及为什么点不了。
     手机的场次卡片、桌面的岗位表、「按岗位看」共用这一份：规则只写一处，
     几种视角下按钮状态一定一致。报名、退选这些动作和「我报了哪」的状态由活动
     详情页 provide('enrollCtx') 给进来，这里只决定显示哪一种。 -->
<template>
  <div class="pa">
    <template v-if="mine">
      <template v-if="mine.status === 'pending_confirm'">
        <el-countdown format="HH:mm:ss" :value="ctx.deadlineTs(g)" />
        <el-button type="primary" size="small" :disabled="ctx.paused.value"
                   @click="ctx.confirmPromo(g)">{{ t('确认') }}</el-button>
        <el-button size="small" plain :disabled="ctx.paused.value"
                   @click="ctx.cancelEnroll(g)">{{ t('放弃') }}</el-button>
      </template>
      <template v-else>
        <el-tag :type="mine.status === 'waitlisted' ? 'warning' : 'success'" size="small">
          {{ mine.status === 'waitlisted' ? `候补第 ${mine.waitlist_position} 位` : '已报名' }}
        </el-tag>
        <el-button v-if="ctx.canCancel(g)" size="small" type="danger" plain
                   :disabled="ctx.paused.value"
                   @click="ctx.cancelEnroll(g)">
          {{ t(mine.status === 'waitlisted' ? '退出候补' : '退选') }}
        </el-button>
      </template>
    </template>
    <!-- 截止后统一分配的场次：报名只从「待分配」进，真实岗位由负责人分 -->
    <span v-else-if="g.needs_assign && !s.is_placeholder" class="dim">{{ t('由负责人分配') }}</span>
    <span v-else-if="g.my_enrollment" class="dim">{{ takenText }}</span>
    <el-button v-else-if="ctx.queuedShift(g)" size="small" disabled
               :loading="ctx.queuedHere(g, s)">
      {{ ctx.queuedHere(g, s) ? '排队中…' : (s.remaining > 0 ? '选中' : '候补') }}
    </el-button>
    <el-button v-else-if="s.eligible === false" size="small" disabled>{{ t('不符合条件') }}</el-button>
    <el-button v-else-if="g.locked || s.list_locked" size="small" disabled>{{ t('名单已锁定') }}</el-button>
    <el-button v-else-if="ctx.paused.value || g.enroll_paused || s.enroll_paused"
               size="small" disabled>{{ t('报名暂停') }}</el-button>
    <el-button v-else-if="ctx.openFor(g)" size="small" type="primary" plain
               @click="ctx.doEnroll(g, s)">
      {{ t(s.is_placeholder ? ctx.enrollText(g) : s.remaining > 0 ? '选中' : '候补') }}
    </el-button>
    <el-button v-else size="small" disabled>{{ ctx.closedText(g) }}</el-button>
  </div>
  <div v-if="showWhy && s.eligible === false && !g.my_enrollment" class="pa-why">
    {{ s.ineligible_reason }}
  </div>
  <div v-if="ctx.queuedHere(g, s)" class="pa-queue">{{ ctx.QUEUE_NOTE }}</div>
</template>

<script setup>
import { computed, inject } from 'vue'
import { t } from '../i18n'

const props = defineProps({
  g: { type: Object, required: true },        // 场次
  s: { type: Object, required: true },        // 岗位
  // 按岗位看时，同一时段别的岗位分在别的卡片里：「同场次只能选一个」看不懂，
  // 直接说这个时段已经报了哪个岗位
  byRole: { type: Boolean, default: false },
  showWhy: { type: Boolean, default: true },  // 桌面岗位表的「要求」列已经有提示
})
const ctx = inject('enrollCtx')

const mine = computed(() => (props.g.my_enrollment
  && props.g.my_enrollment.position_id === props.s.id ? props.g.my_enrollment : null))
const takenText = computed(() => {
  if (!props.byRole) return '同场次只能选一个岗位'
  const p = props.g.positions.find(x => x.id === props.g.my_enrollment.position_id)
  return `这个时段已报了「${p ? p.display_name : '待分配'}」`
})
</script>

<style scoped>
.pa { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.pa-why { margin-top: 8px; font-size: 12px; color: #e6a23c; }
.pa-queue { margin-top: 4px; font-size: 12px; color: #909399; }
</style>
