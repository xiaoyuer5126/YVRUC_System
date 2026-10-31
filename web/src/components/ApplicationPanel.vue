<!-- 报名后筛选：志愿者这边的申请入口。勾意向场次、填报名问卷，一个活动只填一次。 -->
<template>
  <el-card shadow="never" class="apply-card">
    <template v-if="mine">
      <div class="apply-head">
        <b>{{ t('我的申请') }}</b>
        <el-tag :type="STATUS_TYPE[mine.status]" size="small">{{ t(mine.status_label) }}</el-tag>
        <span class="dim">{{ t('提交于 {0}', [fmtDT(mine.created_at)]) }}</span>
      </div>
      <div v-if="mine.leader_willing" class="dim" style="margin-top:4px">{{ t('愿意担任组长') }}</div>
      <div class="dim" style="margin:8px 0 12px">{{ t('意向场次：') }}<el-tag v-for="w in mine.wanted" :key="w.shift_id" size="small" effect="plain"
                style="margin-right:8px">
          {{ w.shift_name }}{{ w.position_name ? ' · ' + wantedNames(w) : '' }}
        </el-tag>
      </div>
      <el-descriptions v-if="answeredQuestions.length" :column="1" size="small" border>
        <el-descriptions-item v-for="q in answeredQuestions" :key="q.id" :label="q.title">
          {{ answerText(q) }}
        </el-descriptions-item>
      </el-descriptions>
      <p v-if="mine.status === 'applied'" class="dim">{{ t('报名截止后由负责人筛选，结果会通过通知告诉你。') }}</p>
      <p v-else-if="mine.status === 'rejected'" class="dim">{{ t('本次名额有限，暂未能安排你参加，期待下次活动再见。') }}</p>
      <el-button v-if="mine.status === 'applied'" size="small" type="danger" plain
                 :disabled="act.editing" @click="withdraw">{{ t('撤回申请') }}</el-button>
    </template>

    <template v-else-if="open">
      <!-- 两步：先勾意向岗位，再按勾中的岗位出题。题目可能只问某几个岗位的人，
           所以第二步的题要等第一步定下来才知道 -->
      <el-steps :active="step" simple style="margin-bottom:12px">
        <el-step :title="t('选择意向岗位')" />
        <el-step :title="t('填写报名问卷')" />
      </el-steps>

      <template v-if="step === 1">
        <p class="dim" style="margin:0 0 8px">{{ t('可以跨场次勾多个，同一场次也可以勾几个岗位；负责人可录取其中一场或几场，每场定一个岗位。') }}</p>
        <el-checkbox-group v-model="form.position_ids">
          <div v-for="g in openShifts" :key="g.id" class="shift-block">
            <div class="shift-name">
              {{ g.name }} · {{ tTime(g.time_text) }}
              <span class="dim">{{ t('（{0}）', [g.location || act.location || t('地点待定')]) }}</span>
              <el-tag v-if="g.flexible_time" size="small" type="success" effect="plain" style="margin-left:4px">{{ t('时间灵活·可多次签到') }}</el-tag>
            </div>
            <!-- 截止后统一分配的场次只能勾「待分配」：真实岗位由负责人分 -->
            <div v-for="p in choosable(g)" :key="p.id" class="pos-line">
              <el-checkbox :value="p.id"
                           :disabled="p.eligible === false || g.enroll_paused || p.enroll_paused
                                      || g.locked || p.list_locked">
                {{ posName(p) }}
                <span class="dim">{{ p.is_placeholder ? '' : t('（{0}）', [`${p.enrolled}/${p.quota}`]) }}</span>
              </el-checkbox>
              <span v-if="g.locked || p.list_locked" class="dim">{{ t('名单已锁定') }}</span>
              <span v-else-if="g.enroll_paused || p.enroll_paused" class="dim">{{ t('暂停报名') }}</span>
              <span v-else-if="p.eligible === false" class="dim">{{ t('不符合条件：{0}', [tTags(p.ineligible_reason)]) }}</span>
            </div>
          </div>
        </el-checkbox-group>
        <el-button type="primary" style="margin-top:12px" :loading="loadingStep"
                   @click="goStep2">{{ t('下一步') }}</el-button>
      </template>

      <template v-else>
        <div class="dim" style="margin-bottom:12px">{{ t('意向岗位：') }}<el-tag v-for="p in chosen" :key="p.id" size="small" effect="plain"
                  style="margin-right:8px">{{ p.shift_name }} · {{ posName(p) }}</el-tag>
        </div>
        <el-form label-position="top">
          <QuestionFields ref="fields" v-model="form.answers" :questions="stepQuestions"
                          :upload-url="`/api/activities/${act.id}/apply/upload`" />
          <el-form-item>
            <el-checkbox v-model="form.leader_willing">{{ t('愿意担任组长（如岗位需要）') }}</el-checkbox>
            <div class="dim" style="width:100%">{{ t('担任小组长的志愿者将根据实际工作额外记录志愿时数。') }}</div>
            <el-checkbox v-model="form.allow_reassign" style="margin-top:8px">{{ t('接受岗位调剂') }}</el-checkbox>
            <div class="dim" style="width:100%">{{ t('仅在名额不足时可能被调到有空缺的岗位；负责人会看到你的意愿。') }}</div>
          </el-form-item>
        </el-form>
        <div class="btn-row">
          <el-button @click="step = 1">{{ t('上一步') }}</el-button>
          <el-button type="primary" :loading="submitting" @click="submit">{{ t('提交申请') }}</el-button>
        </div>
      </template>
    </template>

    <!-- 不在报名期：审批人和路过的人也该看得到这份表长什么样 -->
    <template v-else>
      <el-alert type="info" :closable="false" :title="closedText" />
      <div class="apply-head"><b>{{ t('报名问卷') }}</b>
        <span class="dim">{{ t('报名开放后填写，并勾选意向场次') }}</span>
      </div>
      <ol v-if="questions.length" class="preview">
        <li v-for="q in questions" :key="q.id">
          {{ q.title }}
          <el-tag size="small" effect="plain" style="margin:0 8px">{{ t(q.kind_label) }}</el-tag>
          <el-tag v-if="q.required" size="small" type="warning" effect="plain">{{ t('必填') }}</el-tag>
          <div v-if="q.options?.length" class="dim">{{ t('选项：{0}', [q.options.join(' / ')]) }}</div>
        </li>
      </ol>
      <el-empty v-else :description="t('还没有设置题目')" />
    </template>
  </el-card>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, fmtDT, withConflictConfirm } from '../api'
import QuestionFields from './QuestionFields.vue'
import { posName, t, tTags, tTime } from '../i18n'

const props = defineProps({ act: { type: Object, required: true } })
const emit = defineEmits(['changed'])

const STATUS_TYPE = { applied: 'primary', accepted: 'success', rejected: 'info', withdrawn: 'info' }
const questions = computed(() => props.act.application?.questions || [])
const mine = computed(() => props.act.application?.mine || null)
const open = computed(() => props.act.status === 'published' && !props.act.opens_in
                            && !props.act.editing
                            && props.act.shifts.some(g => g.enroll_open))
const closedText = computed(() => {
  if (props.act.editing) return t('负责人正在调整安排，报名暂停中')
  if (props.act.status !== 'published') return t('不在报名期')
  return props.act.opens_in ? t('未开始报名') : t('报名已截止')
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

/** 意向岗位名（后端用「、」连起来）：占位岗位「待分配」要翻，其余是负责人填的。 */
const wantedNames = w => w.position_name.split('、')
  .map(n => (n === '待分配' ? t(n) : n)).join(t('、'))

function answerText(q) {
  const v = mine.value.answers[q.id]
  if (v === undefined || v === '' || (Array.isArray(v) && !v.length)) return '—'
  if (Array.isArray(v)) return v.join(t('、'))
  return q.kind === 'file' ? t('已上传') : v
}

/** 第一步交完岗位，问后端这几个岗位该答哪些题。 */
async function goStep2() {
  if (!form.position_ids.length) return ElMessage.warning(t('请至少勾选一个意向岗位'))
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
    // 意向场次与已报名的活动时间冲突时先问一句，确认了才带 confirm_conflict 重发
    const d = await withConflictConfirm(confirm => api.post(
      `/api/activities/${props.act.id}/apply`,
      { position_ids: form.position_ids, answers,
        leader_willing: form.leader_willing,
        allow_reassign: form.allow_reassign, confirm_conflict: confirm }),
    { ask: t('仍要提交申请？') })
    if (!d) return
    ElMessage.success(t(d.message))
    emit('changed')
  } finally { submitting.value = false }
}

async function withdraw() {
  await api.del(`/api/activities/${props.act.id}/apply`)
  ElMessage.success(t('已撤回申请'))
  emit('changed')
}
</script>

<style scoped>
.apply-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.shift-block { margin-bottom: 12px; }
/* 场次标题在复选框组里，组上的 font-size: 0 会让这行字不显示：
   自己写回字号与行高，复选框本身的字号由组件自己给 */
.shift-name { font-weight: 600; margin-bottom: 4px; font-size: 14px; line-height: 1.5; }
.pos-line { display: flex; align-items: center; gap: 8px; padding-left: 10px; }
.preview { margin: 8px 0 0; padding-left: 20px; line-height: 1.6; }
</style>
