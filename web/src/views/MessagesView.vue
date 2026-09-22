<template>
  <div class="page page-narrow">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <h2 style="margin:0">消息通知</h2>
      <el-button size="small" @click="readAll" :disabled="!unread">全部已读</el-button>
    </div>
    <el-alert v-if="pendingAck" type="warning" :closable="false" show-icon
              :title="`有 ${pendingAck} 条通知需要您签收`" />
    <el-card v-for="n in items" :key="n.id" shadow="never" class="msg"
             :class="{ unread: !n.read, ack: n.need_ack && !n.acked_at,
                       clickable: n.data?.activity_id || n.data?.route }"
             @click="openMessage(n)">
      <div class="row">
        <el-tag size="small" effect="plain" :type="tagType(n.template_key)">{{ tagLabel(n.template_key) }}</el-tag>
        <el-tag v-if="n.need_ack" size="small"
                :type="n.acked_at ? 'success' : 'danger'"
                :effect="n.acked_at ? 'plain' : 'dark'">
          {{ n.acked_at ? '已签收' : '需签收' }}
        </el-tag>
        <b class="msg-title">{{ n.title }}</b>
        <span class="dim time">{{ fmtDT(n.created_at) }}</span>
      </div>
      <p class="content" style="white-space:pre-wrap">{{ n.content }}</p>
      <div v-if="n.need_ack" class="ack-bar" @click.stop>
        <template v-if="n.acked_at">
          <span class="dim">已于 {{ fmtDT(n.acked_at) }} 签收</span>
        </template>
        <template v-else>
          <el-button type="primary" size="small" :loading="acking === n.id"
                     @click="ack(n)">我已知悉</el-button>
        </template>
      </div>
    </el-card>
    <el-empty v-if="!items.length" description="暂无消息" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, fmtDT } from '../api'

const router = useRouter()

/** 点开一条：先标已读，再去它该去的地方。
 *
 * 待办的审批要进审批页——审批人在活动详情页看不到通过/驳回的按钮；
 * 其余带了活动的就进活动页，候补转正、签到提醒这类看完总要去那儿操作。
 */
async function openMessage(n) {
  if (!n.read) {
    n.read = true
    unread.value = Math.max(0, unread.value - 1)
    api.post(`/api/me/notifications/${n.id}/read`, {}, { silent: true })
      .catch(() => { n.read = false })
  }
  if (n.data?.route === 'approvals') return router.push('/approvals')
  // 时长核准、申诉答复去「我的志愿时长」：确认/申诉的按钮在那儿，活动详情页
  // 上没有，跳过去等于让人自己再找一遍
  if (n.data?.route === 'hours') return router.push('/hours')
  if (n.data?.activity_id) router.push('/activity/' + n.data.activity_id)
}

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
  checkin_reminder: ['info', '签到提醒'],
  hours_approved: ['success', '时长核准'],
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
    ElMessage.success('签收成功')
    load()
  } finally { acking.value = '' }
}
</script>

<style scoped>
.page-narrow { max-width: 760px; }
.msg { margin-bottom: 12px; border-radius: 12px; }
.msg.unread { border-left: 3px solid var(--el-color-primary); }
.msg.ack { border-left: 3px solid var(--el-color-danger); }
.ack-bar {
  display: flex; align-items: center; gap: 12px;
  margin-top: 12px; padding-top: 10px;
  border-top: 1px dashed var(--el-border-color);
}
.row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.msg-title { flex: 1; min-width: 0; }
.time { margin-left: auto; white-space: nowrap; }
.msg.clickable { cursor: pointer; }
.msg.clickable:hover { box-shadow: 0 2px 12px rgba(0, 0, 0, .08); }
/* 手机上一行放不下「类型 + 标题 + 时间」：标题整条落到第二行，
   第一行只留类型标签和时间 */
@media (max-width: 700px) {
  .msg-title { order: 9; flex: 0 0 100%; margin-top: 4px; line-height: 1.5; }
}
.content { margin: 8px 0 0; color: #606266; line-height: 1.75; font-size: 13.5px; }
</style>
