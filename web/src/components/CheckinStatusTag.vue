<!-- 签到状态标签。场次结束后仍未签退的是「未签退」，不是「服务中」。 -->
<template>
  <template v-if="row.checked_in">
    <el-tag type="success" effect="light">已到岗</el-tag>
    <span v-if="row.checkout_at" class="dim">
      签退 {{ row.checkout_at.slice(11, 16) }}
    </span>
    <el-tag v-else-if="ended" type="danger" effect="light" size="small">未签退</el-tag>
    <el-tag v-else type="warning" effect="light" size="small">服务中</el-tag>
  </template>
  <span v-else class="dim">未签到</span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ row: { type: Object, required: true } })

const ended = computed(() => {
  const t = props.row.shift?.end_time
  return !!t && new Date(t) < new Date()
})
</script>
