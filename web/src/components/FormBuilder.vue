<!-- 报名问卷：题目在这里设计（管理页「岗位设置」里的弹窗）。即选即得的活动
     在报名弹窗里答，报名后筛选的申请时填。公开之后锁定。
     智能出题：按活动简介和岗位起草几道题，先列出来给人看，点「加进题目列表」
     才进下面可改的列表，照常点「保存报名问卷」才算数。 -->
<template>
  <div v-loading="loading">
    <div class="fb-head">
      <span class="dim">
        {{ screened ? '志愿者申请时填写，意向场次无需出题'
                    : '选填。志愿者报名时填写，答案在「志愿者管理」中查看' }}
      </span>
      <el-button v-if="editable" type="primary" size="small" :loading="saving"
                 style="margin-left:auto" @click="save">保存报名问卷</el-button>
    </div>
    <el-alert v-if="!editable" type="info" :closable="false"
              title="活动已公开，报名问卷不能再改" />
    <ol v-if="!editable && questions.length" class="fb-readonly">
      <li v-for="q in questions" :key="q.id">
        <b>{{ q.title }}</b>
        <el-tag size="small" effect="plain" style="margin:0 8px">{{ KINDS[q.kind] }}</el-tag>
        <el-tag v-if="q.required" size="small" type="warning" effect="plain">必填</el-tag>
        <div v-if="q.options?.length" class="dim">选项：{{ q.options.join(' / ') }}</div>
        <div v-if="q.applies_to_positions?.length" class="dim">
          只问：{{ q.applies_to_positions.map(positionName).join('、') }}
        </div>
        <div v-if="q.condQ" class="dim">{{ condText(q) }}</div>
      </li>
    </ol>
    <div v-for="(q, i) in questions" v-else :key="q.key" class="fb-row">
      <span class="fb-no">{{ i + 1 }}.</span>
      <el-select v-model="q.kind" style="width:120px" :disabled="!editable"
                 @change="reconcile">
        <el-option v-for="(label, k) in KINDS" :key="k" :value="k" :label="label" />
      </el-select>
      <el-input v-model="q.title" placeholder="题目" maxlength="200"
                :disabled="!editable" class="fb-title" />
      <el-checkbox v-model="q.required" :disabled="!editable">必填</el-checkbox>
      <el-button-group v-if="editable" size="small">
        <el-button :disabled="i === 0" @click="move(i, -1)">上移</el-button>
        <el-button :disabled="i === questions.length - 1" @click="move(i, 1)">下移</el-button>
        <el-button type="danger" plain @click="remove(i)">删除</el-button>
      </el-button-group>
      <el-input v-if="q.kind === 'single' || q.kind === 'multi'" v-model="q.optionsText"
                type="textarea" :rows="2" placeholder="选项，一行一个（至少两个）"
                :disabled="!editable" class="fb-opts" @change="reconcile" />
      <!-- 适用岗位：只问选了这些岗位的人。留空 = 人人都答 -->
      <div v-if="positionGroups.length" class="fb-cond">
        <span class="dim">适用岗位</span>
        <el-select v-model="q.applies_to_positions" multiple collapse-tags
                   collapse-tags-tooltip clearable placeholder="人人都答"
                   style="width:320px" :disabled="!editable">
          <el-option-group v-for="g in positionGroups" :key="g.id" :label="g.name">
            <el-option v-for="p in g.positions" :key="p.id" :value="p.id"
                       :label="p.name" />
          </el-option-group>
        </el-select>
      </div>
      <!-- 显示条件：只能挂在前面某道选择题的某几个选项上 -->
      <div class="fb-cond">
        <span class="dim">显示条件</span>
        <el-select v-model="q.condQ" clearable placeholder="一直显示" style="width:220px"
                   :disabled="!editable" @change="clearCondValues(q)">
          <el-option v-for="c in conditionSources(i)" :key="c.key" :value="c.key"
                     :label="c.title" />
        </el-select>
        <el-select v-if="q.condQ" v-model="q.condValues" multiple collapse-tags
                   style="width:260px" placeholder="选中其中之一时才显示"
                   :disabled="!editable">
          <el-option v-for="o in optionsOf(q.condQ)" :key="o" :value="o" :label="o" />
        </el-select>
      </div>
    </div>
    <div v-if="editable" class="fb-add">
      <el-button plain size="small" @click="add">添加题目</el-button>
      <el-button v-if="aiOn" plain size="small" type="primary"
                 @click="aiOpen ? closeAi() : (aiOpen = true)">智能出题</el-button>
    </div>
    <div v-if="aiOpen" class="fb-ai">
      <div class="dim">
        按活动简介和岗位出题。想重点了解什么可以写在下面，不写也行。
      </div>
      <el-input v-model="aiText" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }"
                maxlength="2000" show-word-limit
                placeholder="如：翻译岗要会英语或日语，想看相关经历；要统计服装尺码" />
      <el-upload v-model:file-list="aiFiles" :auto-upload="false" multiple
                 :limit="AI_FILE_LIMIT" :accept="AI_FILE_ACCEPT">
        <el-button size="small">上传文件（招募要求、立项书等）</el-button>
        <template #tip><div class="dim">{{ AI_FILE_TIP }}</div></template>
      </el-upload>
      <el-alert type="warning" :closable="false" show-icon
                title="活动信息、这里写的话和附件会发给 AI 服务，别传身份证号、志愿者名单这类个人信息。题目可能有错，用之前请逐条核对。" />
      <div class="fb-add">
        <el-button type="primary" size="small" :loading="aiBusy" @click="draftQuestions">
          {{ aiResult ? '重新出题' : '出题' }}
        </el-button>
        <span v-if="aiBusy" class="dim">正在出题，一般要十几秒到一分钟</span>
      </div>
      <template v-if="aiResult">
        <el-alert v-if="aiResult.notes.length" type="info" :closable="false" title="智能起草提醒">
          <div v-for="(n, i) in aiResult.notes" :key="i">· {{ n }}</div>
        </el-alert>
        <div v-if="!aiResult.questions.length" class="dim">没起草出题目，换个说法再试</div>
        <ol v-else class="fb-readonly">
          <li v-for="(q, i) in aiResult.questions" :key="i">
            <b>{{ q.title }}</b>
            <el-tag size="small" effect="plain" style="margin:0 8px">{{ KINDS[q.kind] }}</el-tag>
            <el-tag v-if="q.required" size="small" type="warning" effect="plain">必填</el-tag>
            <div v-if="q.options.length" class="dim">选项：{{ q.options.join(' / ') }}</div>
            <div v-if="q.applies_to_positions.length" class="dim">
              只问：{{ [...new Set(q.applies_to_positions.map(positionName))].join('、') }}
            </div>
          </li>
        </ol>
        <div class="fb-add">
          <el-button size="small" @click="closeAi">先不用</el-button>
          <el-button v-if="aiResult.questions.length" type="primary" size="small"
                     @click="addDrafted">
            加进题目列表（{{ aiResult.questions.length }} 题）
          </el-button>
        </div>
      </template>
    </div>
    <div v-if="editable" class="dim" style="margin-top:8px">
      适用岗位留空 = 人人都答；显示条件只能挂在前面的选择题上。
    </div>
    <el-empty v-else-if="!questions.length" description="没有设置题目" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { AI_FILE_ACCEPT, AI_FILE_LIMIT, AI_FILE_TIP, aiEnabled, aiForm } from '../ai'

const props = defineProps({
  activityId: { type: String, required: true },
  editable: { type: Boolean, default: false },
  screened: { type: Boolean, default: false },   // 报名后筛选：表是申请的一部分
})
// 题数报给外面：弹窗按钮上要写「报名问卷（3 题）」
const emit = defineEmits(['count'])

const KINDS = { single: '单选', multi: '多选', text: '填空', file: '文件上传' }
const questions = ref([])
const positionGroups = ref([])            // 出题时挑「这道题问谁」用
const positionName = id => positionGroups.value
  .flatMap(g => g.positions).find(p => p.id === id)?.name || '已删除的岗位'
const loading = ref(false)
const saving = ref(false)

async function load() {
  loading.value = true
  try {
    const d = await api.get(`/api/staff/activities/${props.activityId}/form`)
    questions.value = d.questions.map(toRow)
    // 占位岗位（「待分配」）不是真岗位，随时会被删掉（开放选岗、切回「报名即选
    // 岗位」），题目挂上去就悬空了，所以不列进下拉；一场真实岗位都没建的场次也
    // 不占一组
    positionGroups.value = (d.shifts || [])
      .map(g => ({ ...g, positions: g.positions.filter(p => !p.is_placeholder) }))
      .filter(g => g.positions.length)
    emit('count', questions.value.length)
  } finally { loading.value = false }
}
onMounted(load)

// key：题目在编辑页上的编号，显示条件按它指向来源题。已保存的题用正式 id，
// 新题先给一个临时编号，不用保存就能被后面的题选作条件
let newSeq = 0
const newKey = () => `new-${++newSeq}`

const toRow = q => ({
  ...q, key: q.id, optionsText: (q.options || []).join('\n'),
  condQ: q.show_if?.question_id || '', condValues: q.show_if?.values || [],
  applies_to_positions: q.applies_to_positions || [],
})

function add() {
  questions.value.push({ id: '', key: newKey(), kind: 'single', title: '', optionsText: '',
                         required: true, condQ: '', condValues: [],
                         applies_to_positions: [] })
}

// 能当条件的：排在这道题前面的单选/多选题。往后指会绕成环
function conditionSources(index) {
  return questions.value.slice(0, index)
    .filter(q => (q.kind === 'single' || q.kind === 'multi') && q.title.trim())
}
const optionsOf = key => (questions.value.find(q => q.key === key)?.optionsText || '')
  .split('\n').map(s => s.trim()).filter(Boolean)
const clearCondValues = q => { q.condValues = [] }
const condText = q => `仅当「${questions.value.find(x => x.key === q.condQ)?.title || ''}」`
  + `选了 ${q.condValues.join(' / ')} 时显示`
function move(i, delta) {
  const list = questions.value
  const [q] = list.splice(i, 1)
  list.splice(i + delta, 0, q)
  reconcile()
}
function remove(i) {
  questions.value.splice(i, 1)
  reconcile()
}

// 来源题挪到后面、删掉、改成非选择题，或者选项改了：当场清掉失效的条件并说一声，
// 不留到保存时才报错。选项只去掉已经不存在的那几个，一个都不剩才整条清掉。
// 选项框在失焦时才对账，免得改字改到一半就把条件清了
function reconcile() {
  const notes = []
  questions.value.forEach((q, i) => {
    if (!q.condQ) return
    const name = q.title.trim() || `第 ${i + 1} 题`
    const at = questions.value.findIndex(x => x.key === q.condQ)
    const src = questions.value[at]
    let why = ''
    if (!src) why = '来源题已删除'
    else if (at > i) why = '来源题挪到了它后面'
    else if (src.kind !== 'single' && src.kind !== 'multi') why = '来源题不再是选择题'
    if (!why) {
      const opts = optionsOf(q.condQ)
      const kept = q.condValues.filter(v => opts.includes(v))
      if (kept.length === q.condValues.length) return
      if (kept.length) {
        notes.push(`「${name}」的显示条件去掉了已不存在的选项：`
                   + q.condValues.filter(v => !opts.includes(v)).join(' / '))
        q.condValues = kept
        return
      }
      why = '选中的选项都已不存在'
    }
    notes.push(`「${name}」的显示条件已清除：${why}`)
    q.condQ = ''
    q.condValues = []
  })
  if (notes.length) ElMessage.warning({ message: notes.join('；'), duration: 6000 })
}

// —— 智能出题 ——
const aiOn = ref(false)
const aiOpen = ref(false)
const aiText = ref('')
const aiFiles = ref([])
const aiBusy = ref(false)
const aiResult = ref(null)      // { questions, notes }
onMounted(async () => { aiOn.value = await aiEnabled() })

async function draftQuestions() {
  aiBusy.value = true
  try {
    aiResult.value = await api.post(`/api/staff/ai/activities/${props.activityId}/form-draft`,
                                    aiForm(aiText.value, aiFiles.value.map(f => f.raw)))
  } catch { /* 错误提示 api 层已经给了 */ } finally {
    aiBusy.value = false
  }
}

function closeAi() {
  aiOpen.value = false
  aiText.value = ''
  aiFiles.value = []
  aiResult.value = null
}

function addDrafted() {
  const drafted = aiResult.value.questions
  for (const q of drafted) {
    questions.value.push({ id: '', key: newKey(), kind: q.kind, title: q.title,
                           required: q.required,
                           optionsText: q.options.join('\n'), condQ: '', condValues: [],
                           applies_to_positions: q.applies_to_positions })
  }
  closeAi()
  ElMessage.success(`已加进 ${drafted.length} 道题，核对后点「保存报名问卷」`)
}

async function save() {
  saving.value = true
  try {
    const body = questions.value.map(q => ({
      id: q.id || '', ref: q.id ? '' : q.key, kind: q.kind, title: q.title, required: q.required,
      options: q.optionsText.split('\n').map(s => s.trim()).filter(Boolean),
      show_if: q.condQ ? { question_id: q.condQ, values: q.condValues } : {},
      applies_to_positions: q.applies_to_positions || [],
    }))
    const d = await api.put(`/api/staff/activities/${props.activityId}/form`,
                            { questions: body })
    questions.value = d.questions.map(toRow)
    emit('count', questions.value.length)
    ElMessage.success('报名问卷已保存')
  } finally { saving.value = false }
}
</script>

<style scoped>
.fb-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.fb-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
          padding: 8px 0; border-bottom: 1px dashed #eceef1; }
.fb-no { width: 22px; color: #909399; }
.fb-title { flex: 1 1 240px; min-width: 0; }
.fb-opts { width: 100%; margin-left: 30px; }
.fb-readonly { margin: 0; padding-left: 20px; line-height: 1.6; }
.fb-cond { width: 100%; margin-left: 30px; display: flex; align-items: center;
           gap: 8px; flex-wrap: wrap; }
.fb-add { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.fb-ai { margin-top: 12px; padding: 12px; border: 1px solid #eceef1; border-radius: 12px;
         display: flex; flex-direction: column; gap: 8px; }
.fb-ai .el-alert { margin: 0; }
</style>
