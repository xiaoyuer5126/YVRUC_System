<!-- 签到状态标签。场次结束后仍未签退的是「未签退」，不是「服务中」。
     时间灵活的场次一张码签一轮，显示一共签过几次，这一轮没签退的标「服务中」。 -->
<template>
  <template v-if="row.shift?.flexible_time && row.checkin_count">
    <el-tag type="success" effect="light">{{ t('已签到 {0} 次', [row.checkin_count]) }}</el-tag>
    <el-tag v-if="row.checked_in && !row.checkout_at" type="warning" effect="light"
            size="small">{{ t('服务中') }}</el-tag>
  </template>
  <template v-else-if="row.checked_in">
    <el-tag type="success" effect="light">{{ t('已到岗') }}</el-tag>
    <span v-if="row.checkout_at" class="dim">{{ t('签退 {0}', [row.checkout_at.slice(11, 16)]) }}</span>
    <el-tag v-else-if="ended" type="danger" effect="light" size="small">{{ t('未签退') }}</el-tag>
    <el-tag v-else type="warning" effect="light" size="small">{{ t('服务中') }}</el-tag>
  </template>
  <span v-else class="dim">{{ t('未签到') }}</span>
</template>

<script setup>
import { computed } from 'vue'
import { t } from '../i18n'

const props = defineProps({ row: { type: Object, required: true } })

const ended = computed(() => {
  // 多时段看此刻那一段的结束时间；没有就退回整场的结束时间
  const end = props.row.shift?.period_end || props.row.shift?.end_time
  return !!end && new Date(end) < new Date()
})
</script>
