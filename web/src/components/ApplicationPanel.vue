<!-- 报名后筛选：志愿者这边的申请入口。勾意向场次、填报名问卷，一个活动只填一次。 -->
<template>
  <el-card shadow="never" class="apply-card">
    <template v-if="mine">
      <div class="apply-head">
        <b>我的申请</b>
        <el-tag :type="STATUS_TYPE[mine.status]" size="small">{{ mine.status_label }}</el-tag>
        <span class="dim">提交于 {{ fmtDT(mine.created_at) }}</span>
      </div>
      <div v-if="mine.leader_willing" class="dim" style="margin-top:4px">
        愿意担任组长
      </div>
      <div class="dim" style="margin:8px 0 12px">
        意向场次：
        <el-tag v-for="w in mine.wanted" :key="w.shift_id" size="small" effect="plain"
                style="margin-right:8px">
          {{ w.shift_name }}{{ w.position_name ? ' · ' + w.position_name : '' }}
        </el-tag>
      </div>
      <el-descriptions v-if="answeredQuestions.length" :column="1" size="small" border>
        <el-descriptions-item v-for="q in answeredQuestions" :key="q.id" :label="q.title">
          {{ answerText(q) }}
        </el-descriptions-item>
      </el-descriptions>
      <p v-if="mine.status === 'applied'" class="dim">
        报名截止后由负责人筛选，结果会通过通知告诉你。
      </p>
      <p v-else-if="mine.status === 'rejected'" class="dim">
        本次名额有限，暂未能安排你参加，期待下次活动再见。
      </p>
      <el-button v-if="mine.status === 'applied'" size="small" type="danger" plain
                 :disabled="act.editing" @click="withdraw">撤回申请</el-button>
    </template>

    <template v-else-if="open">
      <!-- 两步：先勾意向岗位，再按勾中的岗位出题。题目可能只问某几个岗位的人，
           所以第二步的题要等第一步定下来才知道 -->
      <el-steps :active="step" simple style="margin-bottom:12px">
        <el-step title="选择意向岗位" />
        <el-step title="填写报名问卷" />
      </el-steps>

      <template v-if="step === 1">
        <p class="dim" style="margin:0 0 8px">
          可以跨场次勾多个，同一场次也可以勾几个岗位；负责人可录取其中一场或几场，每场定一个岗位。
        </p>
        <el-checkbox-group v-model="form.position_ids">
          <div v-for="g in openShifts" :key="g.id" class="shift-block">
            <div class="shift-name">
              {{ g.name }} · {{ g.time_text }}
              <span class="dim">（{{ g.location || act.location || '地点待定' }}）</span>
            </div>
            <!-- 截止后统一分配的场次只能勾「待分配」：真实岗位由负责人分 -->
            <div v-for="p in choosable(g)" :key="p.id" class="pos-line">
              <el-checkbox :value="p.id"
                           :disabled="p.eligible === false || g.enroll_paused || p.enroll_paused
                                      || g.locked || p.list_locked">
                {{ p.display_name }}
                <span class="dim">{{ p.is_placeholder ? '' : `（${p.enrolled}/${p.quota}）` }}</span>
              </el-checkbox>
              <span v-if="g.locked || p.list_locked" class="dim">名单已锁定</span>
              <span v-else-if="g.enroll_paused || p.enroll_paused" class="dim">暂停报名</span>
              <span v-else-if="p.eligible === false" class="dim">
                不符合条件：{{ p.ineligible_reason }}
              </span>
            </div>
          </div>
        </el-checkbox-group>
        <el-button type="primary" style="margin-top:12px" :loading="loadingStep"
                   @click="goStep2">下一步</el-button>
      </template>

      <template v-else>
        <div class="dim" style="margin-bottom:12px">
          意向岗位：
          <el-tag v-for="p in chosen" :key="p.id" size="small" effect="plain"
                  style="margin-right:8px">{{ p.shift_name }} · {{ p.display_name }}</el-tag>
        </div>
        <el-form label-position="top">
          <QuestionFields ref="fields" v-model="form.answers" :questions="stepQuestions"
                          :upload-url="`/api/activities/${act.id}/apply/upload`" />
          <el-form-item>
            <el-checkbox v-model="form.leader_willing">愿意担任组长（如岗位需要）</el-checkbox>
            <el-checkbox v-model="form.allow_reassign">接受岗位调剂</el-checkbox>
            <div class="dim">仅在名额不足时可能被调到有空缺的岗位；负责人会看到你的意愿。</div>
          </el-form-item>
        </el-form>
        <div class="btn-row">
          <el-button @click="step = 1">上一步</el-button>
          <el-button type="primary" :loading="submitting" @click="submit">提交申请</el-button>
        </div>
      </template>
    </template>

    <!-- 不在报名期：审批人和路过的人也该看得到这份表长什么样 -->
    <template v-else>
      <el-alert type="info" :closable="false" :title="closedText" />
      <div class="apply-head"><b>报名问卷</b>
        <span class="dim">报名开放后填写，并勾选意向场次</span>
      </div>
      <ol v-if="questions.length" class="preview">
        <li v-for="q in questions" :key="q.id">
          {{ q.title }}
          <el-tag size="small" effect="plain" style="margin:0 8px">{{ q.kind_label }}</el-tag>
          <el-tag v-if="q.required" size="small" type="warning" effect="plain">必填</el-tag>
          <div v-if="q.options?.length" class="dim">选项：{{ q.options.join(' / ') }}</div>
        </li>
      </ol>
      <el-empty v-else description="还没有设置题目" />
    </template>
  </el-card>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, fmtDT } from '../api'
import QuestionFields from './QuestionFields.vue'

const props = defineProps({ act: { type: Object, required: true } })
const emit = defineEmits(['changed'])

const STATUS_TYPE = { applied: 'primary', accepted: 'success', rejected: 'info', withdrawn: 'info' }
const questions = computed(() => props.act.application?.questions || [])
const mine = computed(() => props.act.application?.mine || null)
const open = computed(() => props.act.status === 'published' && !props.act.opens_in
                            && !props.act.editing
                            && props.act.shifts.some(g => g.enroll_open))
const closedText = computed(() => {
  if (props.act.editing) return '负责人正在调整安排，报名暂停中'
  if (props.act.status !== 'published') return '不在报名期'
  return props.act.opens_in ? '未开始报名' : '报名已截止'
})

const form = reactive({ position_ids: [], answers: {}, leader_willing: false,
                        allow_reassign: true })
const fields = ref(null)
const step = ref(1)
const stepQuestions = ref([])
const loadingStep = ref(false)

// 还收人的场次才列出来；每个场次列它的岗位，没建岗位的场次只有一个「待分配」
const openShifts = computed(
  () => props.act.shifts.filter(g => g.enroll_open && g.positions.length))
const choosable = g => (g.needs_assign ? g.positions.filter(p => p.is_placeholder)
                                        : g.positions)
const chosen = computed(() => openShifts.value.flatMap(
  g => g.positions.filter(p => form.position_ids.includes(p.id))
    .map(p => ({ ...p, shift_name: g.name }))))
const answeredQuestions = computed(
  () => questions.value.filter(q => q.id in (mine.value?.answers || {})))
const submitting = ref(false)

function answerText(q) {
  const v = mine.value.answers[q.id]
  if (v === undefined || v === '' || (Array.isArray(v) && !v.length)) return '—'
  if (Array.isArray(v)) return v.join('、')
  return q.kind === 'file' ? '已上传' : v
}

/** 第一步交完岗位，问后端这几个岗位该答哪些题。 */
async function goStep2() {
  if (!form.position_ids.length) return ElMessage.warning('请至少勾选一个意向岗位')
  loadingStep.value = true
  try {
    const d = await api.get(`/api/activities/${props.act.id}/form`
                            + `?positions=${form.position_ids.join(',')}`)
    stepQuestions.value = d.questions
    step.value = 2
  } finally { loadingStep.value = false }
}

async function submit() {
  const { error, answers } = fields.value?.collect() || { answers: {} }
  if (error) return ElMessage.warning(error)
  submitting.value = true
  try {
    const d = await api.post(`/api/activities/${props.act.id}/apply`,
                             { position_ids: form.position_ids, answers,
                               leader_willing: form.leader_willing,
                               allow_reassign: form.allow_reassign })
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
.shift-block { margin-bottom: 12px; }
.shift-name { font-weight: 600; margin-bottom: 4px; }
.pos-line { display: flex; align-items: center; gap: 8px; padding-left: 10px; }
.preview { margin: 8px 0 0; padding-left: 20px; line-height: 1.6; }
</style>
