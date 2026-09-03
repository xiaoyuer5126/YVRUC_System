<!-- 报名后筛选：志愿者这边的申请入口。勾意向场次、填报名表，一个活动只填一次。 -->
<template>
  <el-card shadow="never" class="apply-card">
    <template v-if="mine">
      <div class="apply-head">
        <b>我的申请</b>
        <el-tag :type="STATUS_TYPE[mine.status]" size="small">{{ mine.status_label }}</el-tag>
        <span class="dim">提交于 {{ fmtDT(mine.created_at) }}</span>
      </div>
      <div class="dim" style="margin:6px 0 10px">
        意向场次：
        <el-tag v-for="w in mine.wanted" :key="w.shift_id" size="small" effect="plain"
                style="margin-right:6px">{{ w.shift_name }}</el-tag>
      </div>
      <el-descriptions v-if="questions.length" :column="1" size="small" border>
        <el-descriptions-item v-for="q in questions" :key="q.id" :label="q.title">
          {{ answerText(q) }}
        </el-descriptions-item>
      </el-descriptions>
      <p v-if="mine.status === 'applied'" class="dim">
        报名截止后由负责老师筛选，结果会通过通知告诉你。
      </p>
      <p v-else-if="mine.status === 'rejected'" class="dim">
        本次名额有限，暂未能安排你参加，期待下次活动再见。
      </p>
      <el-button v-if="mine.status === 'applied'" size="small" type="danger" plain
                 @click="withdraw">撤回申请</el-button>
    </template>

    <template v-else-if="open">
      <el-form label-position="top">
        <el-form-item label="意向场次（可多选）" required>
          <el-checkbox-group v-model="form.shift_ids">
            <el-checkbox v-for="g in act.shifts" :key="g.id" :value="g.id"
                         :disabled="!g.enroll_open" class="shift-opt">
              {{ g.name }} · {{ g.time_text }}
              <span class="dim">（名额 {{ g.capacity }}{{ g.location ? ' · ' + g.location : '' }}）</span>
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item v-for="q in questions" :key="q.id" :label="q.title" :required="q.required">
          <el-radio-group v-if="q.kind === 'single'" v-model="form.answers[q.id]">
            <el-radio v-for="o in q.options" :key="o" :value="o">{{ o }}</el-radio>
          </el-radio-group>
          <el-checkbox-group v-else-if="q.kind === 'multi'" v-model="form.answers[q.id]">
            <el-checkbox v-for="o in q.options" :key="o" :value="o">{{ o }}</el-checkbox>
          </el-checkbox-group>
          <el-input v-else-if="q.kind === 'text'" v-model="form.answers[q.id]"
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
      </el-form>
      <el-button type="primary" :loading="submitting" @click="submit">提交申请</el-button>
      <span class="dim" style="margin-left:10px">一个活动只填一次；录取前可撤回</span>
    </template>

    <el-empty v-else :image-size="50" :description="closedText" />
  </el-card>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, fmtDT } from '../api'
import { uploadTo } from '../composables/uploadImage'

const props = defineProps({ act: { type: Object, required: true } })
const emit = defineEmits(['changed'])

const STATUS_TYPE = { applied: 'primary', accepted: 'success', rejected: 'info', withdrawn: 'info' }
const questions = computed(() => props.act.application?.questions || [])
const mine = computed(() => props.act.application?.mine || null)
const open = computed(() => props.act.status === 'published' && !props.act.opens_in
                            && props.act.shifts.some(g => g.enroll_open))
const closedText = computed(() => {
  if (props.act.status !== 'published') return '不在报名期'
  return props.act.opens_in ? '未开始报名' : '报名已截止'
})

const form = reactive({ shift_ids: [], answers: {} })
for (const q of questions.value) form.answers[q.id] = q.kind === 'multi' ? [] : ''
const files = reactive({})
const uploading = ref('')
const submitting = ref(false)

function answerText(q) {
  const v = mine.value.answers[q.id]
  if (v === undefined || v === '' || (Array.isArray(v) && !v.length)) return '—'
  if (Array.isArray(v)) return v.join('、')
  return q.kind === 'file' ? '已上传' : v
}

const uploader = q => ({ file }) => uploadFile(q, file)

async function uploadFile(q, file) {
  uploading.value = q.id
  try {
    const d = await uploadTo(`/api/activities/${props.act.id}/apply/upload`, file)
    form.answers[q.id] = d.id
    files[q.id] = d.title
  } catch (e) {
    ElMessage.error(e.message || '上传失败')
  } finally { uploading.value = '' }
}

async function submit() {
  if (!form.shift_ids.length) return ElMessage.warning('请至少勾选一个意向场次')
  for (const q of questions.value) {
    const v = form.answers[q.id]
    if (q.required && (v === '' || v === undefined || (Array.isArray(v) && !v.length))) {
      return ElMessage.warning(`「${q.title}」为必填`)
    }
  }
  submitting.value = true
  try {
    const d = await api.post(`/api/activities/${props.act.id}/apply`,
                             { shift_ids: form.shift_ids, answers: form.answers })
    ElMessage.success(d.message)
    emit('changed')
  } finally { submitting.value = false }
}

async function withdraw() {
  await api.del(`/api/activities/${props.act.id}/apply`)
  ElMessage.success('已撤回申请')
  emit('changed')
}
</script>

<style scoped>
.apply-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.shift-opt { display: flex; align-items: center; height: auto; margin: 4px 0; }
.file-row { display: flex; align-items: center; gap: 10px; }
</style>
