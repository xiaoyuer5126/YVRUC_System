<!-- 意见反馈弹窗：右上角用户菜单（窄屏抽屉里也有）打开，任何身份都能用。
     两类问题分开处理：平台问题由管理员回复；志愿问题由对外联络部回复，可以匿名
     （处理方看不到是谁，但平台记着提交人以便把回复送回来，弹窗里如实写明；完全
     不想留身份的引到校青协的匿名表单）。 -->
<template>
  <el-dialog v-model="visible" :title="t('意见反馈')" :width="narrow ? '94%' : '560px'"
             append-to-body @closed="reset"
             :before-close="guardFeedback" @opened="guardFeedback.snap">
    <el-tabs v-model="tab" @tab-change="onTab">
      <el-tab-pane :label="t('提交反馈')" name="new">
        <div class="kinds">
          <div v-for="k in KINDS" :key="k.value" class="kind"
               :class="{ on: kind === k.value }" @click="kind = k.value">
            <div class="kind-title">{{ t(k.title) }}</div>
            <div class="dim">{{ t(k.hint) }}</div>
          </div>
        </div>

        <el-form v-if="kind === 'volunteer'" label-position="top" class="vol-form">
          <el-form-item :label="t('意见类型')" required>
            <el-radio-group v-model="volKind" class="kind-radios">
              <el-radio v-for="k in VOL_KINDS" :key="k.value" :value="k.value">{{ t(k.label) }}</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item :label="t('关联活动（选填）')">
            <el-select v-model="activityId" filterable remote clearable style="width:100%"
                       :remote-method="searchActivities" :loading="searching"
                       :placeholder="t('先列出你报过名的活动，也可以输入标题搜索')"
                       @visible-change="v => v && !activityOptions.length && searchActivities('')">
              <el-option v-for="a in activityOptions" :key="a.id" :value="a.id" :label="a.title" />
            </el-select>
          </el-form-item>
        </el-form>

        <el-input v-model="content" type="textarea" :rows="6"
                  :maxlength="MAX_CHARS" show-word-limit
                  :placeholder="kind === 'platform'
                    ? t('请描述遇到的问题：在哪个页面、做了什么、看到了什么。可附截图。')
                    : t('请写下你的意见或问题')" />
        <div class="shots-cap dim">{{ t('截图（可选，最多 {0} 张，单张不超过 {1} MB）', [MAX_IMAGES, MAX_MB]) }}</div>
        <el-upload v-model:file-list="files" list-type="picture-card"
                   :auto-upload="false" :limit="MAX_IMAGES" multiple
                   accept="image/png,image/jpeg,image/webp"
                   :on-change="onPick" :on-exceed="onExceed"
                   :class="{ full: files.length >= MAX_IMAGES }">
          <el-icon><Plus /></el-icon>
        </el-upload>

        <template v-if="kind === 'volunteer'">
          <el-input v-model="contact" :maxlength="CONTACT_MAX" class="contact"
                    :placeholder="t('联系方式（选填），如手机号、微信号')" />
          <el-checkbox v-model="anonymous" class="anon">{{ t('匿名提交') }}</el-checkbox>
          <div class="dim anon-note">
            {{ t('勾选后，处理反馈的同学看不到你的姓名、学号和学院；平台仍会记录提交人，以便把回复送到你这里。完全不想留下身份的，请填') }}
            <a :href="VOLUNTEER_URL" target="_blank" rel="noopener noreferrer">{{ t('匿名表单') }}</a>{{ t('。') }}
          </div>
        </template>

        <div class="foot">
          <el-button @click="visible = false">{{ t('取消') }}</el-button>
          <el-button type="primary" :loading="saving" @click="submit">{{ t('提交') }}</el-button>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('我的反馈')" name="mine">
        <div v-loading="loading" class="mine">
          <el-empty v-if="!mine.length && !loading" :description="t('还没有提交过反馈')" />
          <div v-for="f in mine" :key="f.id" class="item">
            <div class="item-head">
              <el-tag size="small" :type="f.status === 'handled' ? 'success' : 'warning'">
                {{ t(f.status_label) }}
              </el-tag>
              <el-tag size="small" effect="plain">
                {{ f.category === 'volunteer' ? t(f.kind_label) : t('平台问题') }}
              </el-tag>
              <span v-if="f.anonymous" class="dim">{{ t('匿名') }}</span>
              <span class="dim">{{ fmtDT(f.created_at) }}</span>
            </div>
            <div v-if="f.activity" class="dim">{{ t('关联活动：{0}', [f.activity.title]) }}</div>
            <div class="text">{{ f.content }}</div>
            <FeedbackShots :images="f.images" />
            <div v-if="f.status === 'handled'" class="reply">
              <div class="dim">
                {{ f.category === 'volunteer' ? t('青协回复（{0}）', [fmtDT(f.handled_at)])
                  : t('管理员回复（{0}）', [fmtDT(f.handled_at)]) }}
              </div>
              <div class="text">{{ f.reply || t('已处理') }}</div>
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
import { useCloseGuard } from '../composables/closeGuard'
import FeedbackShots from './FeedbackShots.vue'
import { t } from '../i18n'

// 与后端 config.FEEDBACK_* 一致；以后端校验为准，这里只是提前拦一下
const MAX_CHARS = 2000
const MAX_IMAGES = 3
const MAX_MB = 5
// 校青协的匿名反馈表单：完全不想留下身份的人用它
const VOLUNTEER_URL = 'http://ruczyzfeedback.mikecrm.com/RYUXyfs'
// 存中文原句，模板里再 t()：要是在这里就翻好，切换语言后这两项不会跟着换
const KINDS = [
  { value: 'platform', title: '反馈平台问题', hint: '页面报错、功能用不了、操作不顺手' },
  { value: 'volunteer', title: '反馈志愿问题', hint: '活动问题、志愿咨询、对青协的建议' },
]
// 与后端 models.Feedback.KIND_LABELS 一致
const VOL_KINDS = [
  { value: 'nature', label: '存在不符合志愿性质的活动' },
  { value: 'issue', label: '反映某志愿活动中存在的问题' },
  { value: 'ask', label: '询问志愿相关问题' },
  { value: 'suggest', label: '提出对于青协工作的改进建议或想法' },
  { value: 'resource', label: '推荐校内外志愿服务资源、活动创意等' },
  { value: 'other', label: '其他' },
]
const CONTACT_MAX = 64

const narrow = useNarrow()
const visible = ref(false)
const tab = ref('new')
const kind = ref('platform')
const content = ref('')
const files = ref([])
const volKind = ref('')
const activityId = ref('')
const activityOptions = ref([])
const searching = ref(false)
const contact = ref('')
const anonymous = ref(false)
const guardFeedback = useCloseGuard(() => [kind.value, content.value, files.value.length,
  volKind.value, activityId.value, contact.value, anonymous.value])
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
  volKind.value = ''
  activityId.value = ''
  activityOptions.value = []
  contact.value = ''
  anonymous.value = false
}

async function searchActivities(q) {
  searching.value = true
  try {
    activityOptions.value = await api.get(`/api/feedback/activities?q=${encodeURIComponent(q || '')}`)
  } finally { searching.value = false }
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
    ElMessage.warning(okType ? t('单张截图不能超过 {0} MB', [MAX_MB]) : t('截图只支持 png/jpg/webp 格式'))
    files.value = files.value.filter(f => f.uid !== file.uid)
  }
}

function onExceed() {
  ElMessage.warning(t('截图最多 {0} 张', [MAX_IMAGES]))
}

async function submit() {
  const text = content.value.trim()
  const volunteer = kind.value === 'volunteer'
  if (volunteer && !volKind.value) return ElMessage.warning(t('请选择意见类型'))
  if (!text) return ElMessage.warning(volunteer ? t('请填写意见内容') : t('请填写反馈内容'))
  const form = new FormData()
  form.append('content', text)
  form.append('category', kind.value)
  if (volunteer) {
    form.append('kind', volKind.value)
    form.append('activity_id', activityId.value || '')
    form.append('contact', contact.value.trim())
    form.append('anonymous', anonymous.value ? 'true' : 'false')
  }
  files.value.forEach(f => form.append('files', f.raw))
  saving.value = true
  try {
    // 表单带文件，不走 api 的 JSON 封装
    const resp = await fetch('/api/feedback', {
      method: 'POST', body: form, headers: authHeaders(),
    })
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      ElMessage.error(typeof data.detail === 'string' ? t(data.detail) : t('提交失败({0})', [resp.status]))
      return
    }
    ElMessage.success(t('已提交，感谢反馈！处理后会通过站内消息通知你'))
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
  border: 1px solid #dcdfe6; border-radius: 4px; padding: 12px; cursor: pointer;
  line-height: 1.6;
}
.kind.on { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.kind-title { font-weight: 600; }
.shots-cap { margin: 12px 0 8px; }
.vol-form :deep(.el-form-item) { margin-bottom: 12px; }
.kind-radios { display: flex; flex-direction: column; align-items: flex-start; }
.contact { margin-top: 12px; }
.anon { margin-top: 8px; }
.anon-note { line-height: 1.6; }
.full :deep(.el-upload--picture-card) { display: none; }
.foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.mine { min-height: 120px; }
.item { border-bottom: 1px dashed #eceef1; padding: 12px 0; }
.item-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.text { white-space: pre-wrap; word-break: break-word; line-height: 1.6; margin-bottom: 8px; }
.reply { background: #f5f7fa; border-radius: 4px; padding: 8px 12px; margin-top: 8px; }
@media (max-width: 860px) {
  .kinds { grid-template-columns: 1fr; }
}
</style>
