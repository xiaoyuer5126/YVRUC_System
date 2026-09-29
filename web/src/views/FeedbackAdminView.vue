<template>
  <div class="page page-wide">
    <h2>意见反馈</h2>
    <p class="dim" style="margin-top:-8px">
      用户从右上角「意见反馈」提交的平台问题。处理并回复后，提交人会收到站内消息。
      志愿工作相关的问题不在这里收，已引导到校青协志愿工作反馈平台。
    </p>
    <el-card shadow="never">
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
      <el-pagination layout="prev, pager, next" :total="total" :page-size="SIZE"
                     v-model:current-page="page" @current-change="load"
                     style="margin-top:12px;justify-content:flex-end" />
    </el-card>

    <el-dialog v-model="handling" title="处理反馈" :width="narrow ? '94%' : '520px'">
      <div class="text dim">{{ current?.content }}</div>
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
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import FeedbackShots from '../components/FeedbackShots.vue'

const SIZE = 20
const narrow = useNarrow()
const items = ref([])
const total = ref(0)
const openCount = ref(0)
const page = ref(1)
const status = ref('open')
const loading = ref(false)
const handling = ref(false)
const current = ref(null)
const reply = ref('')
const saving = ref(false)

async function load() {
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
</script>

<style scoped>
.list { min-height: 120px; }
.item { border-bottom: 1px dashed #eceef1; padding: 12px 0; }
.item-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.act { margin-left: auto; }
.text { white-space: pre-wrap; word-break: break-word; line-height: 1.7; margin-bottom: 8px; }
.reply { background: #f5f7fa; border-radius: 6px; padding: 8px 12px; margin-top: 8px; }
</style>
