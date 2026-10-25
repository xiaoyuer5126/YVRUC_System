<!-- 全站统一的分页条。
     桌面显示总数与页码；窄屏下缩成小号、去掉页码，只留总数与上一页/下一页——
     手指点页码太密，容易点错相邻的数字。翻页只抛事件，当前页由调用方自己改。 -->
<template>
  <el-pagination class="pager" :size="narrow ? 'small' : 'default'"
                 :layout="narrow ? 'total, prev, next' : 'total, prev, pager, next'"
                 :current-page="page" :page-size="pageSize" :total="total"
                 @current-change="p => emit('change', p)" />
</template>

<script setup>
import { useNarrow } from '../composables/useNarrow'

defineProps({
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 20 },
  total: { type: Number, default: 0 },
})
const emit = defineEmits(['change'])
const narrow = useNarrow()
</script>

<style scoped>
.pager { margin-top: 12px; justify-content: flex-end; }
</style>
