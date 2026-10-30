<!-- 报名状态标签。表格与窄屏卡片共用，免得两处各写一套判断。 -->
<template>
  <el-tag v-if="row.status === 'applied'" type="primary">{{ t('待筛选') }}</el-tag>
  <el-tag v-else-if="row.status === 'enrolled'" type="success">{{ t('已报名') }}</el-tag>
  <el-tag v-else-if="row.status === 'waitlisted'" type="warning">
    {{ row.waitlist_position ? `候补第 ${row.waitlist_position} 位` : '候补中' }}
  </el-tag>
  <el-countdown v-else-if="row.status === 'pending_confirm'" format="HH:mm:ss"
                :value="deadlineTs">
    <template #prefix><el-tag type="danger">{{ t('转正确认剩') }}</el-tag>&nbsp;</template>
  </el-countdown>
  <el-tag v-else-if="row.cancel_type === 'locked'" type="danger">{{ t('锁定后退出') }}</el-tag>
  <el-tag v-else type="info">{{ cancelLabel }}</el-tag>
</template>

<script setup>
import { computed } from 'vue'
import { t } from '../i18n'

const props = defineProps({ row: { type: Object, required: true } })

const CANCEL_LABELS = {
  normal: '已退选', abandoned: '已放弃候补', expired: '确认超时',
  rejected: '未录取',
}

const cancelLabel = computed(() => CANCEL_LABELS[props.row.cancel_type] || '已取消')

const deadlineTs = computed(() => {
  const d = props.row.confirm_deadline
  return d ? new Date(d.replace(' ', 'T')).getTime() : 0
})
</script>
