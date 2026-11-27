<!-- 志愿者反馈的处理端：会长团与对外联络部骨干、部长在「活动审批」的页签里用，管理员在
     「意见反馈」的页签里用。匿名的反馈后端不返回提交人，这里只显示「匿名志愿者」。
     回复框预填后端给的模板（reply_draft），处理人把【解决方法】换成具体内容。 -->
<template>
  <div>
    <div class="toolbar">
      <el-radio-group v-model="status" size="small" @change="reload">
        <el-radio-button value="open">待处理</el-radio-button>
        <el-radio-button value="handled">已处理</el-radio-button>
        <el-radio-button value="">全部</el-radio-button>
      </el-radio-group>
      <el-select v-model="kind" size="small" clearable placeholder="意见类型"
                 style="width:220px" @change="reload">
        <el-option v-for="k in kinds" :key="k.value" :value="k.value" :label="k.label" />
      </el-select>
      <el-select v-model="activityId" size="small" clearable filterable placeholder="关联活动"
                 style="width:220px" @change="reload">
        <el-option v-for="a in activities" :key="a.id" :value="a.id" :label="a.title" />
      </el-select>
      <span class="dim">共 {{ total }} 条 · 待处理 {{ openCount }} 条</span>
    </div>

    <div v-loading="loading" class="list">
      <el-empty v-if="!items.length && !loading" description="暂无志愿者反馈" />
      <div v-for="f in items" :key="f.id" class="item">
        <div class="item-head">
          <el-tag size="small" :type="f.status === 'handled' ? 'success' : 'warning'">
            {{ f.status_label }}
          </el-tag>
          <el-tag size="small" effect="plain">{{ f.kind_label }}</el-tag>
          <template v-if="f.user">
            <b>{{ f.user.name }}</b>
            <span class="dim">{{ f.user.student_id }} · {{ f.user.college || '—' }}</span>
          </template>
          <b v-else>匿名志愿者</b>
          <span class="dim">{{ fmtDT(f.created_at) }}</span>
          <el-button class="act" size="small" :type="f.status === 'handled' ? 'default' : 'primary'"
                     @click="openReply(f)">
            {{ f.status === 'handled' ? '修改回复' : '回复' }}
          </el-button>
        </div>
        <div v-if="f.activity" class="meta">
          <span class="dim">关联活动：</span>
          <router-link :to="`/activity/${f.activity.id}`">{{ f.activity.title }}</router-link>
        </div>
        <div v-if="f.contact" class="meta"><span class="dim">联系方式：</span>{{ f.contact }}</div>
        <div class="text">{{ f.content }}</div>
        <FeedbackShots :images="f.images" />
        <div v-if="f.status === 'handled'" class="reply">
          <div class="dim">{{ f.handled_by_name || '处理人' }} 回复于 {{ fmtDT(f.handled_at) }}</div>
          <div class="text">{{ f.reply }}</div>
        </div>
      </div>
    </div>
    <Pager :page="page" :page-size="SIZE" :total="total" @change="goPage" />

    <el-dialog v-model="replying" title="回复志愿者反馈" :width="narrow ? '94%' : '560px'"
               append-to-body :before-close="guardReply" @opened="guardReply.snap">
      <div class="dim">【{{ current?.kind_label }}】{{ current?.content }}</div>
      <el-input v-model="reply" type="textarea" :rows="9" maxlength="2000" show-word-limit
                class="reply-box" placeholder="回复内容，会通过站内消息发给提交人" />
      <template #footer>
        <el-button @click="replying = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">发送回复</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import { useCloseGuard } from '../composables/closeGuard'
import FeedbackShots from './FeedbackShots.vue'
import Pager from './Pager.vue'

const SIZE = 20
const PLACEHOLDER = '【解决方法】'
const narrow = useNarrow()
const items = ref([])
const total = ref(0)
const openCount = ref(0)
const kinds = ref([])
const activities = ref([])
const page = ref(1)
const status = ref('open')
const kind = ref('')
const activityId = ref('')
const loading = ref(false)
const replying = ref(false)
const current = ref(null)
const reply = ref('')
const guardReply = useCloseGuard(() => reply.value)
const saving = ref(false)

async function load() {
  loading.value = true
  try {
    const q = new URLSearchParams({ page: page.value, size: SIZE })
    if (status.value) q.set('status', status.value)
    if (kind.value) q.set('kind', kind.value)
    if (activityId.value) q.set('activity_id', activityId.value)
    const d = await api.get(`/api/staff/volunteer-feedback?${q}`)
    items.value = d.items
    total.value = d.total
    openCount.value = d.open
    kinds.value = d.kinds
    activities.value = d.activities
  } finally { loading.value = false }
}

function reload() {
  page.value = 1
  load()
}

function goPage(p) {
  page.value = p
  load()
}

function openReply(f) {
  current.value = f
  reply.value = f.reply || f.reply_draft
  replying.value = true
}

async function save() {
  const text = reply.value.trim()
  if (!text) return ElMessage.warning('请填写回复内容')
  if (text.includes(PLACEHOLDER)) {
    try {
      await ElMessageBox.confirm(`回复里还留着「${PLACEHOLDER}」，没有换成具体的解决方法。仍要发送吗？`,
        '确认发送', { type: 'warning', confirmButtonText: '仍要发送', cancelButtonText: '回去修改' })
    } catch { return }
  }
  saving.value = true
  try {
    await api.post(`/api/staff/volunteer-feedback/${current.value.id}/reply`, { reply: text })
    ElMessage.success('已回复，已通知提交人')
    replying.value = false
    load()
  } finally { saving.value = false }
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px; }
.list { min-height: 120px; }
.item { border-bottom: 1px dashed #eceef1; padding: 12px 0; }
.item-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.act { margin-left: auto; }
.meta { margin-bottom: 4px; line-height: 1.6; }
.text { white-space: pre-wrap; word-break: break-word; line-height: 1.6; margin-bottom: 8px; }
.reply { background: #f5f7fa; border-radius: 4px; padding: 8px 12px; margin-top: 8px; }
.reply-box { margin-top: 8px; }
</style>
