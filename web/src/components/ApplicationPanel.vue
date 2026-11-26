<!-- 报名后筛选：志愿者这边的申请入口。勾意向岗位、填报名问卷，一个活动只填一次。 -->
<template>
  <el-card shadow="never" class="apply-card">
    <template v-if="mine">
      <div class="apply-head">
        <b>{{ t('我的申请') }}</b>
        <el-tag :type="STATUS_TYPE[mine.status]" size="small">{{ t(mine.status_label) }}</el-tag>
        <span class="dim">{{ t('提交于 {0}', [fmtDT(mine.created_at)]) }}</span>
      </div>
      <div v-if="mine.leader_willing" class="dim" style="margin-top:4px">{{ t('愿意担任组长') }}</div>
      <!-- 同名岗位可能勾了几个时段：一行一条，带上时段才分得清 -->
      <div class="dim" style="margin:8px 0 12px">{{ t('意向岗位：') }}
        <div v-for="w in mine.wanted" :key="w.shift_id">
          {{ w.shift_name }} · {{ tTime(w.time_text) }}
        </div>
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
        <p class="dim" style="margin:0 0 8px">{{ t('可以勾多个岗位；负责人可录取其中一个或几个。') }}</p>
        <!-- 一条可报的一行，按时间排：普通岗位写岗位名；统一分配只能勾它的报名入口，
             写成「上午场志愿者」，真实岗位由负责人分 -->
        <el-checkbox-group v-model="form.position_ids">
          <div v-for="x in options" :key="x.p.id" class="pos-line">
            <el-checkbox :value="x.p.id"
                         :disabled="x.p.eligible === false || x.g.enroll_paused || x.p.enroll_paused
                                    || x.g.locked || x.p.list_locked">
              {{ x.label }}
            </el-checkbox>
            <span>{{ tTime(x.g.time_text) }}</span>
            <span class="dim">{{ x.p.location || x.g.location || act.location || t('地点待定') }}</span>
            <span v-if="!x.p.is_placeholder" class="dim">{{ `${x.p.enrolled}/${x.p.quota}` }}</span>
            <span v-if="x.g.needs_assign" class="dim">{{ t('报名后统一安排具体岗位') }}</span>
            <el-tag v-if="x.g.periods.length > 1" size="small" type="warning" effect="plain">{{ t('共 {0} 个时段 · 需全部参加', [x.g.periods.length]) }}</el-tag>
            <el-tag v-if="x.g.flexible_time" size="small" type="success" effect="plain">{{ t('时间灵活·可多次签到') }}</el-tag>
            <span v-if="x.g.locked || x.p.list_locked" class="dim">{{ t('名单已锁定') }}</span>
            <span v-else-if="x.g.enroll_paused || x.p.enroll_paused" class="dim">{{ t('暂停报名') }}</span>
            <span v-else-if="x.p.eligible === false" class="dim">{{ t('不符合条件：{0}', [tTags(x.p.ineligible_reason)]) }}</span>
          </div>
        </el-checkbox-group>
        <el-button type="primary" style="margin-top:12px" :loading="loadingStep"
                   @click="goStep2">{{ t('下一步') }}</el-button>
      </template>

      <template v-else>
        <div class="dim" style="margin-bottom:12px">{{ t('意向岗位：') }}<el-tag v-for="p in chosen" :key="p.id" size="small" effect="plain"
                  style="margin-right:8px">{{ p.label }}</el-tag>
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
        <span class="dim">{{ t('报名开放后填写，并勾选意向岗位') }}</span>
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
import { t, tTags, tTime } from '../i18n'

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

/** 统一分配的叫法，和活动详情页一致：「上午场志愿者」 */
const assignName = g => (g.name.endsWith('志愿者') ? g.name : t('{0}志愿者', [g.name]))
// 还收人的才列出来，按时间排。普通岗位一岗一场；统一分配只能勾占位的「待分配」
const options = computed(() => props.act.shifts
  .filter(g => g.enroll_open)
  .flatMap(g => g.positions
    .filter(p => (g.needs_assign ? p.is_placeholder : !p.is_placeholder))
    .map(p => ({ g, p, label: g.needs_assign ? assignName(g) : p.name })))
  .sort((a, b) => a.g.start_time.localeCompare(b.g.start_time)))
// 第二步顶上那排：同名岗位勾了几个时段时带上时段才分得清
const chosen = computed(() => options.value
  .filter(x => form.position_ids.includes(x.p.id))
  .map(x => ({ id: x.p.id, label: `${x.label} · ${tTime(x.g.time_text)}` })))
const answeredQuestions = computed(
  () => questions.value.filter(q => q.id in (mine.value?.answers || {})))
const submitting = ref(false)

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
    // 意向岗位与已报名的活动时间冲突时先问一句，确认了才带 confirm_conflict 重发
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
/* 复选框组上的 font-size: 0 会让行里的字不显示：自己写回字号与行高，
   复选框本身的字号由组件自己给 */
.pos-line { display: flex; align-items: center; gap: 4px 8px; flex-wrap: wrap;
            margin-bottom: 8px; font-size: 13px; line-height: 1.5; }
.preview { margin: 8px 0 0; padding-left: 20px; line-height: 1.6; }
</style>
