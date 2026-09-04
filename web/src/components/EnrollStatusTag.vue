<!-- 报名状态标签。表格与窄屏卡片共用，免得两处各写一套判断。 -->
<template>
  <el-tag v-if="row.status === 'applied'" type="primary">待筛选</el-tag>
  <el-tag v-else-if="row.status === 'enrolled'" type="success">已报名</el-tag>
  <el-tag v-else-if="row.status === 'waitlisted'" type="warning">
    候补中{{ row.waitlist_position ? ` 第${row.waitlist_position}位` : '' }}
  </el-tag>
  <el-countdown v-else-if="row.status === 'pending_confirm'" format="HH:mm:ss"
                :value="deadlineTs">
    <template #prefix><el-tag type="danger">转正确认剩</el-tag>&nbsp;</template>
  </el-countdown>
  <template v-else-if="row.cancel_type === 'late'">
    <el-tag type="danger">临近退选</el-tag>
    <div v-if="row.seat_held" class="dim">名额已扣留，不可再报</div>
  </template>
  <el-tag v-else type="info">{{ cancelLabel }}</el-tag>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ row: { type: Object, required: true } })

const CANCEL_LABELS = {
  normal: '已退选', abandoned: '已放弃候补', expired: '确认超时',
  shift_deleted: '场次已取消', rejected: '未录取',
}

const cancelLabel = computed(() => CANCEL_LABELS[props.row.cancel_type] || '已取消')

const deadlineTs = computed(() => {
  const d = props.row.confirm_deadline
  return d ? new Date(d.replace(' ', 'T')).getTime() : 0
})
</script>
