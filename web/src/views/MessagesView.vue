<template>
  <div class="page">
    <div class="page-head">
      <h2>消息通知</h2>
      <el-button @click="readAll" :disabled="!unread">全部已读</el-button>
    </div>
    <el-alert v-if="pendingAck" type="warning" :closable="false" show-icon class="ack-alert"
              :title="`有 ${pendingAck} 条通知需要您签收`" />
    <!-- 电脑端两栏，新消息在前（从左到右、从上到下）；同一行的两张卡片等高，
         上下间距才处处一样 -->
    <div v-if="items.length" class="msg-list">
      <el-card v-for="n in items" :key="n.id" shadow="never" class="msg"
               :class="{ unread: !n.read, ack: n.need_ack && !n.acked_at,
                         clickable: n.page }"
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
    </div>
    <div v-if="hasMore" class="more">
      <el-button :loading="loadingMore" @click="loadMore">加载更多</el-button>
    </div>
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
 * 去哪一页由后端按通知算好（n.page，规则见 msgcenter.page_for）。企业微信
 * 卡片的点击链接出自同一份规则，站内和企业微信里点同一条不会各去各的。
 */
async function openMessage(n) {
  if (!n.read) {
    n.read = true
    unread.value = Math.max(0, unread.value - 1)
    api.post(`/api/me/notifications/${n.id}/read`, {}, { silent: true })
      .catch(() => { n.read = false })
  }
  if (n.page) router.push(n.page)
}

const items = ref([])
const unread = ref(0)
const pendingAck = ref(0)
const acking = ref('')
const page = ref(1)
const hasMore = ref(false)
const loadingMore = ref(false)
const SIZE = 20

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

function apply(d) {
  unread.value = d.unread
  pendingAck.value = d.pending_ack
  hasMore.value = d.has_more
}

async function load() {
  const d = await api.get(`/api/me/notifications?page=1&size=${SIZE}`)
  page.value = 1
  items.value = d.items
  apply(d)
}
onMounted(load)

/** 接着往下翻一页。翻的过程中来了新通知，后面的会整体往后挪一位，
 * 下一页开头就会是已经显示过的那条：按 id 去重 */
async function loadMore() {
  loadingMore.value = true
  try {
    const d = await api.get(`/api/me/notifications?page=${page.value + 1}&size=${SIZE}`)
    page.value += 1
    const seen = new Set(items.value.map(n => n.id))
    items.value.push(...d.items.filter(n => !seen.has(n.id)))
    apply(d)
  } finally { loadingMore.value = false }
}

// 下面两个只改本地已加载的那几条，不重新拉：重拉会把翻过的页收回第一页
async function readAll() {
  await api.post('/api/me/notifications/read-all')
  items.value.forEach(n => { n.read = true })
  unread.value = 0
}

async function ack(n) {
  acking.value = n.id
  try {
    const d = await api.post(`/api/me/notifications/${n.id}/ack`)
    ElMessage.success('签收成功')
    if (!n.read) unread.value = Math.max(0, unread.value - 1)
    n.read = true
    n.acked_at = d.acked_at
    pendingAck.value = Math.max(0, pendingAck.value - 1)
  } finally { acking.value = '' }
}
</script>

<style scoped>
.ack-alert { margin-bottom: 12px; }
.msg-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 860px) {
  .msg-list { grid-template-columns: minmax(0, 1fr); }
}
.msg { border-radius: 12px; }
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
.more { display: flex; justify-content: center; margin-top: 12px; }
.content { margin: 8px 0 0; color: #606266; line-height: 1.6; font-size: 13.5px; }
</style>
