<!-- 报名问卷答题区：即选即得的报名弹窗与报名后筛选的申请表共用。
     题目按显示条件逐题露出；被挡掉的题不填也不提交，后端按同一套规则再算一次。 -->
<template>
  <el-form-item v-for="q in shown" :key="q.id" :label="q.title" :required="q.required">
    <el-radio-group v-if="q.kind === 'single'" v-model="answers[q.id]">
      <el-radio v-for="o in q.options" :key="o" :value="o">{{ o }}</el-radio>
    </el-radio-group>
    <el-checkbox-group v-else-if="q.kind === 'multi'" v-model="answers[q.id]">
      <el-checkbox v-for="o in q.options" :key="o" :value="o">{{ o }}</el-checkbox>
    </el-checkbox-group>
    <el-input v-else-if="q.kind === 'text'" v-model="answers[q.id]"
              type="textarea" :rows="2" maxlength="2000" show-word-limit />
    <div v-else class="file-row">
      <el-upload :show-file-list="false" :http-request="uploader(q)">
        <el-button size="small" plain :loading="uploading === q.id">
          {{ files[q.id] ? '重新上传' : '上传文件' }}
        </el-button>
      </el-upload>
      <span v-if="files[q.id]" class="dim">{{ files[q.id] }}</span>
    </div>
  </el-form-item>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { uploadTo } from '../composables/uploadImage'

const props = defineProps({
  questions: { type: Array, default: () => [] },
  uploadUrl: { type: String, required: true },   // 文件题的上传接口
})
const answers = defineModel({ type: Object, default: () => ({}) })

// 多选题的答案是数组，先占好位，勾选组件才有东西可写
watch(() => props.questions, qs => {
  for (const q of qs) {
    if (!(q.id in answers.value)) answers.value[q.id] = q.kind === 'multi' ? [] : ''
  }
}, { immediate: true })

const shown = computed(() => {
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
    ElMessage.error(e.message || '上传失败')
  } finally { uploading.value = '' }
}

const isEmpty = v => v === '' || v === undefined || v === null || (Array.isArray(v) && !v.length)

/** 收齐当前显示的题的答案；必填没填就返回 { error }。 */
function collect() {
  const out = {}
  for (const q of shown.value) {
    const v = answers.value[q.id]
    if (q.required && isEmpty(v)) return { error: `「${q.title}」为必填` }
    if (!isEmpty(v)) out[q.id] = v
  }
  return { answers: out }
}
defineExpose({ collect })
</script>

<style scoped>
.file-row { display: flex; align-items: center; gap: 12px; }
</style>
