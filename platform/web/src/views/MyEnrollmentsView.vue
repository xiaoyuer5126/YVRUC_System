<template>
  <div class="page">
    <h2>我的报名</h2>
    <el-table :data="rows" style="width:100%" v-loading="loading">
      <el-table-column label="活动" min-width="200">
        <template #default="{ row }">
          <b>{{ row.activity.title }}</b>
          <div class="dim">{{ row.activity.location }}</div>
        </template>
      </el-table-column>
      <el-table-column label="场次 / 工位" min-width="180">
        <template #default="{ row }">
          {{ row.group.name }}（{{ row.group.time_text }}）
          <div v-if="row.station">
            <el-tag size="small" effect="plain">{{ row.station.name }}</el-tag>
            <span class="dim"> {{ row.station.time_text }} · {{ row.station.location }}</span>
          </div>
          <div v-else-if="row.status === 'confirmed'" class="dim">工位待分配</div>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="190">
        <template #default="{ row }">
          <el-tag v-if="row.status === 'confirmed'" type="success">已报名</el-tag>
          <el-tag v-else-if="row.status === 'waitlisted'" type="warning">候补中</el-tag>
          <template v-else-if="row.status === 'pending_confirm'">
            <el-countdown format="HH:mm:ss"
                          :value="new Date(row.confirm_deadline.replace(' ','T')).getTime()"
                          style="margin-right:8px">
              <template #prefix><el-tag type="danger">转正确认剩</el-tag>&nbsp;</template>
            </el-countdown>
          </template>
          <el-tag v-else-if="row.cancel_type === 'late'" type="danger">临近退选(扣分)</el-tag>
          <el-tag v-else type="info">{{ cancelLabel(row) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="签到" width="100">
        <template #default="{ row }">
          <template v-if="row.checked_in && row.status === 'confirmed'">
            <el-tag type="success" effect="light">已到岗</el-tag>
            <div v-if="row.checkout_at" class="dim checkout-time">签退 {{ row.checkout_at.slice(11, 16) }}</div>
            <el-tag v-else type="warning" effect="light" size="small">服务中</el-tag>
          </template>
          <span v-else-if="row.status === 'confirmed'" class="dim">未签到</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="$router.push('/activity/' + row.activity.id)">查看</el-button>
          <el-button v-if="row.status === 'pending_confirm'" size="small" type="primary"
                     @click="confirm(row)">确认</el-button>
          <el-button v-else-if="row.active !== false && isActive(row)" size="small" type="danger" plain
                     @click="cancel(row)">
            {{ row.status === 'waitlisted' ? '退出候补' : '退选' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-alert type="info" :closable="false" style="margin-top:16px">
      <p style="margin:0;font-size:12.5px;line-height:1.9">
        信用规则：开始前 <b>24 小时以上</b>退选无惩罚，名额自动递补给候补；
        <b>24 小时内</b>退选扣除信用分 5 分且名额不释放；放弃候补连续 3 次暂停本学期候补资格；
        信用分低于 60 分冻结报名 30 天。
      </p>
    </el-alert>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

const rows = ref([])
const loading = ref(false)

const CANCEL_LABELS = {
  normal: '已退选', abandoned: '已放弃候补', expired: '确认超时',
  group_deleted: '场次取消',
}
const cancelLabel = r => r.status === 'cancelled'
  ? (CANCEL_LABELS[r.cancel_type] || '已取消') : ''

function isActive(r) {
  return ['confirmed', 'waitlisted', 'pending_confirm'].includes(r.status)
}

async function load() {
  loading.value = true
  try { rows.value = await api.get('/api/me/enrollments') } finally { loading.value = false }
}
onMounted(load)

async function confirm(row) {
  const d = await api.post(`/api/activities/enrollments/${row.id}/confirm-promotion`)
  ElMessage.success(d.message)
  load()
}

async function cancel(row) {
  const g = row.group
  const start = new Date(g.start_time.replace(' ', 'T'))
  const late = Date.now() > start.getTime() - 24 * 3600e3
  await ElMessageBox.confirm(
    late ? '距开始不足24小时：退选将扣信用分5分，名额不释放给候补。确定吗？'
         : '正常退选不扣分，名额将自动递补候补队列。确定退选吗？',
    '退选确认', { type: 'warning' })
  const d = await api.del(`/api/activities/enrollments/${row.id}`)
  ElMessage[d.effect === 'penalized' ? 'warning' : 'success'](d.message)
  load()
}
</script>

<style scoped>
.checkout-time {
  font-size: 11px;
  line-height: 1.4;
  margin-top: 2px;
}
</style>
