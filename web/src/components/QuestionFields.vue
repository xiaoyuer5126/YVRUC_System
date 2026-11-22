<!-- 报名问卷答题区：即选即得的报名弹窗与报名后筛选的申请表共用。
     题目按显示条件逐题露出；被挡掉的题不填也不提交，后端按同一套规则再算一次。
     reused 里的题（同一活动之前的报名答过）不再露出，答案由后端并进来。 -->
<template>
  <div v-if="reusedCount" class="dim reused-note">
    {{ t('已沿用你之前填写的 {0} 题答案', [reusedCount]) }}
  </div>
  <el-form-item v-for="q in asked" :key="q.id" :label="q.title" :required="q.required">
    <el-radio-group v-if="q.kind === 'single'" v-model="answers[q.id]">
      <el-radio v-for="o in q.options" :key="o" :value="o">{{ o }}</el-radio>
    </el-radio-group>
    <el-checkbox-group v-else-if="q.kind === 'multi'" v-model="answers[q.id]">
      <el-checkbox v-for="o in q.options" :key="o" :value="o">{{ o }}</el-checkbox>
    </el-checkbox-group>
    <el-input v-else-if="q.kind === 'text'" v-model="answers[q.id]"
              type="textarea" :rows="2" maxlength="2000" show-word-limit />
    <div v-else class="file-row">
      <el-upload :show-file-list="false" :http-request="uploader(q)" :accept="accept">
        <el-button size="small" plain :loading="uploading === q.id">
          {{ t(files[q.id] ? '重新上传' : '上传文件') }}
        </el-button>
      </el-upload>
      <span v-if="files[q.id]" class="dim">{{ files[q.id] }}</span>
      <div v-if="rules" class="dim file-tip">
        {{ t('单个不超过 {0}MB，本活动最多传 {1} 个、合计 {2}MB；可传 {3}',
             [rules.max_mb, rules.max_count, rules.total_mb, rules.exts.join(' / ')]) }}
      </div>
    </div>
  </el-form-item>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { acceptOf, answerFileRules } from '../composables/answerFile'
import { uploadTo } from '../composables/uploadImage'
import { t } from '../i18n'

const props = defineProps({
  questions: { type: Array, default: () => [] },
  uploadUrl: { type: String, required: true },   // 文件题的上传接口
  // 之前的报名答过的题：{题目 id: 答案}。调用方要把它们一并放进 v-model 的答案里，
  // 后面的题的显示条件才判得出来
  reused: { type: Object, default: () => ({}) },
})
const answers = defineModel({ type: Object, default: () => ({}) })

// 多选题的答案是数组，先占好位，勾选组件才有东西可写
watch(() => props.questions, qs => {
  for (const q of qs) {
    if (!(q.id in answers.value)) answers.value[q.id] = q.kind === 'multi' ? [] : ''
  }
}, { immediate: true })

const shown = computed(() => {   // vuecheck: script-only（模板经 asked / reusedCount 读它）
  const seen = new Set()
  const out = []
  for (const q of props.questions) {
    const cond = q.show_if || {}
    if (cond.question_id) {
      if (!seen.has(cond.question_id)) continue
      const a = answers.value[cond.question_id]
      const picked = Array.isArray(a) ? a : [a]
      if (!picked.some(v => (cond.values || []).includes(v))) continue
    }
    seen.add(q.id)
    out.push(q)
  }
  return out
})

// 显示条件挡住的题不算沿用：它这次本来就不问
const asked = computed(() => shown.value.filter(q => !(q.id in props.reused)))
const reusedCount = computed(() => shown.value.length - asked.value.length)

// 文件题的大小、个数、格式限制（后端常量），上传前就告诉志愿者
const rules = ref(null)
const accept = computed(() => acceptOf(rules.value))
onMounted(async () => { rules.value = await answerFileRules() })

const files = reactive({})
const uploading = ref('')
const uploader = q => ({ file }) => uploadFile(q, file)
async function uploadFile(q, file) {
  uploading.value = q.id
  try {
    const d = await uploadTo(props.uploadUrl, file)
    answers.value[q.id] = d.id
    files[q.id] = d.title
  } catch (e) {
    ElMessage.error(e.message || t('上传失败'))
  } finally { uploading.value = '' }
}

const isEmpty = v => v === '' || v === undefined || v === null || (Array.isArray(v) && !v.length)

/** 收齐这次问到的题的答案；必填没填就返回 { error }。沿用的题不交，后端自己并。 */
function collect() {
  const out = {}
  for (const q of asked.value) {
    const v = answers.value[q.id]
    if (q.required && isEmpty(v)) return { error: t('「{0}」为必填', [q.title]) }
    if (!isEmpty(v)) out[q.id] = v
  }
  return { answers: out }
}
defineExpose({ collect })
</script>

<style scoped>
.file-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.file-tip { width: 100%; line-height: 1.5; }
.reused-note { margin-bottom: 12px; }
</style>
