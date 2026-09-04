<!-- 报名后筛选：志愿者这边的申请入口。勾意向场次、填报名表，一个活动只填一次。 -->
<template>
  <el-card shadow="never" class="apply-card">
    <template v-if="mine">
      <div class="apply-head">
        <b>我的申请</b>
        <el-tag :type="STATUS_TYPE[mine.status]" size="small">{{ mine.status_label }}</el-tag>
        <span class="dim">提交于 {{ fmtDT(mine.created_at) }}</span>
      </div>
      <div v-if="mine.leader_willing" class="dim" style="margin-top:4px">
        已表态愿意担任组长
      </div>
      <div class="dim" style="margin:6px 0 10px">
        意向场次：
        <el-tag v-for="w in mine.wanted" :key="w.shift_id" size="small" effect="plain"
                style="margin-right:6px">
          {{ w.shift_name }}{{ w.position_name ? ' · ' + w.position_name : '' }}
        </el-tag>
      </div>
      <el-descriptions v-if="answeredQuestions.length" :column="1" size="small" border>
        <el-descriptions-item v-for="q in answeredQuestions" :key="q.id" :label="q.title">
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
          <div style="width:100%">
            <el-checkbox-group v-model="form.shift_ids">
              <el-checkbox v-for="g in act.shifts" :key="g.id" :value="g.id"
                           :disabled="!g.enroll_open" class="shift-opt">
                {{ g.name }} · {{ g.time_text }}
                <span class="dim">（名额 {{ g.capacity }}{{ g.location ? ' · ' + g.location : '' }}）</span>
              </el-checkbox>
            </el-checkbox-group>
            <!-- 「报名即选岗位」的场次要连岗位一起勾；统一分配的录取后再排 -->
            <div v-for="g in pickShifts" :key="g.id" class="pos-pick">
              <span>{{ g.name }} 的意向岗位</span>
              <el-select v-model="form.positions[g.id]" placeholder="选择岗位"
                         style="width:260px">
                <el-option v-for="p in realPositions(g)" :key="p.id" :value="p.id"
                           :disabled="p.eligible === false"
                           :label="`${p.name}（${p.enrolled}/${p.quota}）`" />
              </el-select>
            </div>
          </div>
        </el-form-item>
        <el-form-item v-for="q in shownQuestions" :key="q.id" :label="q.title"
                      :required="q.required">
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
        <el-form-item>
          <div>
            <el-checkbox v-model="form.leader_willing">愿意担任组长（如岗位需要）</el-checkbox>
            <div class="dim">组长由负责老师从有意愿的人里指定，不勾也不影响录取</div>
          </div>
        </el-form-item>
      </el-form>
      <el-button type="primary" :loading="submitting" @click="submit">提交申请</el-button>
      <span class="dim" style="margin-left:10px">一个活动只填一次；录取前可撤回</span>
    </template>

    <!-- 不在报名期：审批人和路过的人也该看得到这份表长什么样 -->
    <template v-else>
      <el-alert type="info" :closable="false" :title="closedText" style="margin-bottom:12px" />
      <div class="apply-head"><b>报名表</b>
        <span class="dim">报名开放后填写，并勾选意向场次</span>
      </div>
      <ol v-if="questions.length" class="preview">
        <li v-for="q in questions" :key="q.id">
          {{ q.title }}
          <el-tag size="small" effect="plain" style="margin:0 6px">{{ q.kind_label }}</el-tag>
          <el-tag v-if="q.required" size="small" type="warning" effect="plain">必填</el-tag>
          <div v-if="q.options?.length" class="dim">选项：{{ q.options.join(' / ') }}</div>
        </li>
      </ol>
      <el-empty v-else description="还没有设置题目" :image-size="50" />
    </template>
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

const form = reactive({ shift_ids: [], positions: {}, answers: {},
                        leader_willing: false })
for (const q of questions.value) form.answers[q.id] = q.kind === 'multi' ? [] : ''

// 只显示条件成立的题。条件挂在前面的选择题上，一遍扫下来就够了；
// 被挡掉的题不填、也不提交答案——后端按同一套规则再算一次。
const shownQuestions = computed(() => {
  const shown = new Set()
  const out = []
  for (const q of questions.value) {
    const cond = q.show_if || {}
    if (cond.question_id) {
      if (!shown.has(cond.question_id)) continue
      const a = form.answers[cond.question_id]
      const picked = Array.isArray(a) ? a : [a]
      if (!picked.some(v => (cond.values || []).includes(v))) continue
    }
    shown.add(q.id)
    out.push(q)
  }
  return out
})

// 需要勾岗位的场次：勾中了、且是「报名即选岗位」
const pickShifts = computed(() => props.act.shifts.filter(
  g => form.shift_ids.includes(g.id) && g.mode !== 'assign'))
const realPositions = g => g.positions.filter(p => !p.is_placeholder)
const answeredQuestions = computed(
  () => questions.value.filter(q => q.id in (mine.value?.answers || {})))
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
  for (const g of pickShifts.value) {
    if (!form.positions[g.id]) return ElMessage.warning(`请为「${g.name}」选择意向岗位`)
  }
  const answers = {}
  for (const q of shownQuestions.value) {
    const v = form.answers[q.id]
    if (q.required && (v === '' || v === undefined || (Array.isArray(v) && !v.length))) {
      return ElMessage.warning(`「${q.title}」为必填`)
    }
    answers[q.id] = v
  }
  submitting.value = true
  try {
    const d = await api.post(`/api/activities/${props.act.id}/apply`,
                             { shift_ids: form.shift_ids, answers,
                               positions: form.positions,
                               leader_willing: form.leader_willing })
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
.pos-pick { display: flex; align-items: center; gap: 10px; margin-top: 6px; }
.file-row { display: flex; align-items: center; gap: 10px; }
.preview { margin: 6px 0 0; padding-left: 20px; line-height: 2; }
</style>
