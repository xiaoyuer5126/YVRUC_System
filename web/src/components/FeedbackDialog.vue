<!-- 意见反馈弹窗：右上角用户菜单（窄屏抽屉里也有）打开，任何身份都能用。
     两类问题分开走：平台问题在这里提交、管理员回复；志愿工作上的问题归校青协
     管，引到他们的反馈平台去，平台上不收。 -->
<template>
  <el-dialog v-model="visible" title="意见反馈" :width="narrow ? '94%' : '560px'"
             append-to-body @closed="reset">
    <el-tabs v-model="tab" @tab-change="onTab">
      <el-tab-pane label="提交反馈" name="new">
        <div class="kinds">
          <div v-for="k in KINDS" :key="k.value" class="kind"
               :class="{ on: kind === k.value }" @click="kind = k.value">
            <div class="kind-title">{{ k.title }}</div>
            <div class="dim">{{ k.hint }}</div>
          </div>
        </div>

        <template v-if="kind === 'platform'">
          <el-input v-model="content" type="textarea" :rows="6"
                    :maxlength="MAX_CHARS" show-word-limit
                    placeholder="请描述遇到的问题：在哪个页面、做了什么、看到了什么。可附截图。" />
          <div class="shots-cap dim">截图（可选，最多 {{ MAX_IMAGES }} 张，单张不超过 {{ MAX_MB }}MB）</div>
          <el-upload v-model:file-list="files" list-type="picture-card"
                     :auto-upload="false" :limit="MAX_IMAGES" multiple
                     accept="image/png,image/jpeg,image/webp"
                     :on-change="onPick" :on-exceed="onExceed"
                     :class="{ full: files.length >= MAX_IMAGES }">
            <el-icon><Plus /></el-icon>
          </el-upload>
          <div class="foot">
            <el-button @click="visible = false">取消</el-button>
            <el-button type="primary" :loading="saving" @click="submit">提交</el-button>
          </div>
        </template>

        <template v-else-if="kind === 'volunteer'">
          <el-alert type="info" :closable="false" show-icon
                    title="志愿工作相关的问题（活动安排、时长认定、组织协调等）请到校青协志愿工作反馈平台反馈，由校青协统一处理。" />
          <div class="foot">
            <el-button type="primary" tag="a" :href="VOLUNTEER_URL" target="_blank"
                       rel="noopener noreferrer">
              打开校青协志愿工作反馈平台
            </el-button>
          </div>
        </template>
      </el-tab-pane>

      <el-tab-pane label="我的反馈" name="mine">
        <div v-loading="loading" class="mine">
          <el-empty v-if="!mine.length && !loading" description="还没有提交过反馈" />
          <div v-for="f in mine" :key="f.id" class="item">
            <div class="item-head">
              <el-tag size="small" :type="f.status === 'handled' ? 'success' : 'warning'">
                {{ f.status_label }}
              </el-tag>
              <span class="dim">{{ fmtDT(f.created_at) }}</span>
            </div>
            <div class="text">{{ f.content }}</div>
            <FeedbackShots :images="f.images" />
            <div v-if="f.status === 'handled'" class="reply">
              <div class="dim">管理员回复（{{ fmtDT(f.handled_at) }}）</div>
              <div class="text">{{ f.reply || '已处理' }}</div>
            </div>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { api, authHeaders, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import FeedbackShots from './FeedbackShots.vue'

// 与后端 config.FEEDBACK_* 一致；以后端校验为准，这里只是提前拦一下
const MAX_CHARS = 2000
const MAX_IMAGES = 3
const MAX_MB = 5
const VOLUNTEER_URL = 'http://ruczyzfeedback.mikecrm.com/RYUXyfs'
const KINDS = [
  { value: 'platform', title: '反馈平台问题', hint: '页面报错、功能用不了、操作不顺手' },
  { value: 'volunteer', title: '反馈志愿问题', hint: '活动安排、时长认定、组织协调' },
]

const narrow = useNarrow()
const visible = ref(false)
const tab = ref('new')
const kind = ref('platform')
const content = ref('')
const files = ref([])
const saving = ref(false)
const mine = ref([])
const loading = ref(false)

function open(which = 'new') {
  tab.value = which
  visible.value = true
  if (which === 'mine') loadMine()
}

function reset() {
  kind.value = 'platform'
  content.value = ''
  files.value = []
}

function onTab(name) {
  if (name === 'mine') loadMine()
}

async function loadMine() {
  loading.value = true
  try {
    mine.value = await api.get('/api/feedback/mine')
  } finally { loading.value = false }
}

function onPick(file) {
  const okType = ['image/png', 'image/jpeg', 'image/webp'].includes(file.raw?.type)
  const okSize = file.size <= MAX_MB * 1024 * 1024
  if (!okType || !okSize) {
    ElMessage.warning(okType ? `单张截图不能超过 ${MAX_MB}MB` : '截图只支持 png/jpg/webp 格式')
    files.value = files.value.filter(f => f.uid !== file.uid)
  }
}

function onExceed() {
  ElMessage.warning(`截图最多 ${MAX_IMAGES} 张`)
}

async function submit() {
  const text = content.value.trim()
  if (!text) return ElMessage.warning('请填写反馈内容')
  const form = new FormData()
  form.append('content', text)
  files.value.forEach(f => form.append('files', f.raw))
  saving.value = true
  try {
    // 表单带文件，不走 api 的 JSON 封装
    const resp = await fetch('/api/feedback', {
      method: 'POST', body: form, headers: authHeaders(),
    })
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      ElMessage.error(typeof data.detail === 'string' ? data.detail : `提交失败(${resp.status})`)
      return
    }
    ElMessage.success('已提交，感谢反馈！处理后会通过站内消息通知你')
    reset()
    tab.value = 'mine'
    loadMine()
  } finally { saving.value = false }
}

defineExpose({ open })
</script>

<style scoped>
.kinds { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }
.kind {
  border: 1px solid #dcdfe6; border-radius: 8px; padding: 12px; cursor: pointer;
  line-height: 1.6;
}
.kind.on { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.kind-title { font-weight: 600; }
.shots-cap { margin: 12px 0 8px; }
.full :deep(.el-upload--picture-card) { display: none; }
.foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.mine { min-height: 120px; }
.item { border-bottom: 1px dashed #eceef1; padding: 12px 0; }
.item-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.text { white-space: pre-wrap; word-break: break-word; line-height: 1.6; margin-bottom: 8px; }
.reply { background: #f5f7fa; border-radius: 6px; padding: 8px 12px; margin-top: 8px; }
@media (max-width: 860px) {
  .kinds { grid-template-columns: 1fr; }
  /* 这里只有两个标签，窄屏也放得下。全局那条「← 左右滑动查看更多功能 →」
     （styles.css 给所有 el-tabs 加的）在这一行是错的提示，去掉 */
  :deep(.el-tabs__header)::after { display: none; }
}
</style>
