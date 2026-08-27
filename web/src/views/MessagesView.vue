<template>
  <div class="page page-narrow">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
      <h2 style="margin:0">消息通知</h2>
      <el-button size="small" @click="readAll" :disabled="!unread">全部已读</el-button>
    </div>
    <p class="dim" style="margin-top:-8px">
      以下为「企业微信模板消息」的送达记录（演示环境在平台内展示；生产环境同步推送到企业微信）。
    </p>
    <el-alert v-if="pendingAck" type="warning" :closable="false" show-icon
              style="margin-bottom:12px"
              :title="`有 ${pendingAck} 条通知需要您签收`"
              description="签收是向负责老师确认「我已看到」；只是打开消息不算签收。" />
    <el-card v-for="n in items" :key="n.id" shadow="never" class="msg"
             :class="{ unread: !n.read, ack: n.need_ack && !n.acked_at }">
      <div class="row">
        <el-tag size="small" effect="plain" :type="tagType(n.template_key)">{{ tagLabel(n.template_key) }}</el-tag>
        <el-tag v-if="n.need_ack" size="small"
                :type="n.acked_at ? 'success' : 'danger'"
                :effect="n.acked_at ? 'plain' : 'dark'">
          {{ n.acked_at ? '已签收' : '需签收' }}
        </el-tag>
        <b>{{ n.title }}</b>
        <span class="dim" style="margin-left:auto">{{ fmtDT(n.created_at) }}</span>
      </div>
      <p class="content" style="white-space:pre-wrap">{{ n.content }}</p>
      <div v-if="n.need_ack" class="ack-bar">
        <template v-if="n.acked_at">
          <span class="dim">已于 {{ fmtDT(n.acked_at) }} 签收</span>
        </template>
        <template v-else>
          <el-button type="primary" size="small" :loading="acking === n.id"
                     @click="ack(n)">我已知悉</el-button>
          <span class="dim">负责老师会看到谁还没签收</span>
        </template>
      </div>
    </el-card>
    <el-empty v-if="!items.length" description="暂无消息" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, fmtDT } from '../api'

const items = ref([])
const unread = ref(0)
const pendingAck = ref(0)
const acking = ref('')

const TAGS = {
  signup_success: ['success', '报名成功'],
  waitlist_queued: ['warning', '进入候补'],
  waitlist_promoted: ['danger', '候补转正'],
  assignment: ['primary', '岗位分配'],
  activity_changed: ['warning', '活动变更'],
  credit_penalty: ['danger', '信用变动'],
  checkin_reminder: ['info', '签到提醒'],
}
const tagType = k => TAGS[k]?.[0] || 'info'
const tagLabel = k => TAGS[k]?.[1] || '通知'

async function load() {
  const d = await api.get('/api/me/notifications')
  items.value = d.items
  unread.value = d.unread
  pendingAck.value = d.pending_ack
}
onMounted(load)

async function readAll() {
  await api.post('/api/me/notifications/read-all')
  load()
}

async function ack(n) {
  acking.value = n.id
  try {
    await api.post(`/api/me/notifications/${n.id}/ack`)
    ElMessage.success('已签收，负责老师可以看到了')
    load()
  } finally { acking.value = '' }
}
</script>

<style scoped>
.page-narrow { max-width: 760px; }
.msg { margin-bottom: 10px; border-radius: 12px; }
.msg.unread { border-left: 3px solid var(--el-color-primary); }
.msg.ack { border-left: 3px solid var(--el-color-danger); }
.ack-bar {
  display: flex; align-items: center; gap: 10px;
  margin-top: 10px; padding-top: 10px;
  border-top: 1px dashed var(--el-border-color);
}
.row { display: flex; align-items: center; gap: 8px; }
.content { margin: 8px 0 0; color: #606266; line-height: 1.75; font-size: 13.5px; }
</style>
