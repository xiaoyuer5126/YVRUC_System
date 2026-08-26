<template>
  <div class="page page-narrow">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
      <h2 style="margin:0">消息通知</h2>
      <el-button size="small" @click="readAll" :disabled="!unread">全部已读</el-button>
    </div>
    <p class="dim" style="margin-top:-8px">
      以下为「企业微信模板消息」的送达记录（演示环境在平台内展示；生产环境同步推送到企业微信）。
    </p>
    <el-card v-for="n in items" :key="n.id" shadow="never" class="msg"
             :class="{ unread: !n.read }">
      <div class="row">
        <el-tag size="small" effect="plain" :type="tagType(n.template_key)">{{ tagLabel(n.template_key) }}</el-tag>
        <b>{{ n.title }}</b>
        <span class="dim" style="margin-left:auto">{{ fmtDT(n.created_at) }}</span>
      </div>
      <p class="content" style="white-space:pre-wrap">{{ n.content }}</p>
    </el-card>
    <el-empty v-if="!items.length" description="暂无消息" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api, fmtDT } from '../api'

const items = ref([])
const unread = ref(0)

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
}
onMounted(load)

async function readAll() {
  await api.post('/api/me/notifications/read-all')
  load()
}
</script>

<style scoped>
.page-narrow { max-width: 760px; }
.msg { margin-bottom: 10px; border-radius: 12px; }
.msg.unread { border-left: 3px solid var(--el-color-primary); }
.row { display: flex; align-items: center; gap: 8px; }
.content { margin: 8px 0 0; color: #606266; line-height: 1.75; font-size: 13.5px; }
</style>
