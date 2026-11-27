<template>
  <div class="page page-wide">
    <h2>意见反馈</h2>
    <!-- 管理员两个页签都有；会长团与对外联络部骨干、部长只处理志愿者反馈，
         平台问题的接口也只给管理员 -->
    <el-tabs v-model="tab">
      <el-tab-pane v-if="isAdmin" label="平台问题" name="platform" />
      <el-tab-pane label="志愿者反馈" name="volunteer" />
    </el-tabs>
    <el-card v-if="tab === 'volunteer'" shadow="never">
      <p class="dim page-note">
        志愿者在平台上提交的志愿工作意见，由对外联络部、会长团和管理员回复。匿名提交的不显示姓名学号。
      </p>
      <VolunteerFeedbackPanel />
    </el-card>
    <el-card v-else shadow="never">
      <p class="dim page-note">
        用户提交的平台问题，处理并回复后，反馈者会收到通知。
      </p>
      <div class="toolbar">
        <el-radio-group v-model="status" @change="reload">
          <el-radio-button value="open">待处理</el-radio-button>
          <el-radio-button value="handled">已处理</el-radio-button>
          <el-radio-button value="">全部</el-radio-button>
        </el-radio-group>
        <span class="dim">共 {{ total }} 条 · 待处理 {{ openCount }} 条</span>
      </div>
      <div v-loading="loading" class="list">
        <el-empty v-if="!items.length && !loading" description="暂无反馈" />
        <div v-for="f in items" :key="f.id" class="item">
          <div class="item-head">
            <el-tag size="small" :type="f.status === 'handled' ? 'success' : 'warning'">
              {{ f.status_label }}
            </el-tag>
            <b>{{ f.user?.name }}</b>
            <span class="dim">{{ f.user?.student_id }} · {{ f.user?.college || '—' }}</span>
            <span class="dim">{{ fmtDT(f.created_at) }}</span>
            <el-button class="act" size="small" :type="f.status === 'handled' ? 'default' : 'primary'"
                       @click="openHandle(f)">
              {{ f.status === 'handled' ? '修改回复' : '处理' }}
            </el-button>
          </div>
          <div class="text">{{ f.content }}</div>
          <FeedbackShots :images="f.images" />
          <div v-if="f.status === 'handled'" class="reply">
            <div class="dim">{{ f.handled_by_name || '管理员' }} 回复于 {{ fmtDT(f.handled_at) }}</div>
            <div class="text">{{ f.reply || '（未填写回复）' }}</div>
          </div>
        </div>
      </div>
      <Pager :page="page" :page-size="SIZE" :total="total" @change="goPage" />
    </el-card>

    <el-dialog v-model="handling" title="处理反馈" :width="narrow ? '94%' : '520px'"
               :before-close="guardReply" @opened="guardReply.snap">
      <div class="text dim">{{ current?.content }}</div>
      <!-- 常用语：点一下整段替换进回复框（见 replyTemplates.js） -->
      <div class="tpl-row">
        <span class="dim">常用语：</span>
        <el-tag v-for="t in feedbackReplies()" :key="t.label" class="tpl" effect="plain"
                @click="reply = t.text">{{ t.label }}</el-tag>
      </div>
      <el-input v-model="reply" type="textarea" :rows="5" maxlength="2000" show-word-limit
                placeholder="回复内容（可选），会通过站内消息发给提交人" />
      <template #footer>
        <el-button @click="handling = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">标记已处理并通知</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import { useCloseGuard } from '../composables/closeGuard'
import FeedbackShots from '../components/FeedbackShots.vue'
import Pager from '../components/Pager.vue'
import VolunteerFeedbackPanel from '../components/VolunteerFeedbackPanel.vue'
import { feedbackReplies } from '../replyTemplates'
import { auth } from '../store'

const SIZE = 20
const narrow = useNarrow()
const route = useRoute()
const isAdmin = computed(() => auth.activeIdentity === 'admin')
// 志愿反馈的通知点开带 ?tab=volunteer；不是管理员的只有这一个页签
const tab = ref(!isAdmin.value || route.query.tab === 'volunteer' ? 'volunteer' : 'platform')
watch(() => route.query.tab, v => { if (v === 'volunteer') tab.value = 'volunteer' })
// 切身份后页面不重开：失去管理员身份就退到志愿者反馈
watch(isAdmin, v => { if (!v) tab.value = 'volunteer' })
const items = ref([])
const total = ref(0)
const openCount = ref(0)
const page = ref(1)
const status = ref('open')
const loading = ref(false)
const handling = ref(false)
const current = ref(null)
const reply = ref('')
const guardReply = useCloseGuard(() => reply.value)
const saving = ref(false)

async function load() {
  if (!isAdmin.value) return
  loading.value = true
  try {
    const d = await api.get(`/api/staff/feedback?page=${page.value}&size=${SIZE}`
      + (status.value ? `&status=${status.value}` : ''))
    items.value = d.items
    total.value = d.total
    openCount.value = d.open
  } finally { loading.value = false }
}

function reload() {
  page.value = 1
  load()
}

function openHandle(f) {
  current.value = f
  reply.value = f.reply || ''
  handling.value = true
}

async function save() {
  saving.value = true
  try {
    await api.post(`/api/staff/feedback/${current.value.id}/handle`, { reply: reply.value })
    ElMessage.success('已处理，已通知提交人')
    handling.value = false
    load()
  } finally { saving.value = false }
}

onMounted(load)
// 从志愿者反馈切回平台问题时，页面打开时若没加载过就补一次
watch(tab, v => { if (v === 'platform' && !items.value.length) load() })

function goPage(p) {
  page.value = p
  load()
}
</script>

<style scoped>
.list { min-height: 120px; }
.item { border-bottom: 1px dashed #eceef1; padding: 12px 0; }
.item-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.act { margin-left: auto; }
.text { white-space: pre-wrap; word-break: break-word; line-height: 1.6; margin-bottom: 8px; }
.reply { background: #f5f7fa; border-radius: 4px; padding: 8px 12px; margin-top: 8px; }
.tpl-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0; }
.tpl { cursor: pointer; }
</style>
