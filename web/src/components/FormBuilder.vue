<!-- 报名后筛选的报名表：题目在这里设计，志愿者申请时填。公开之后锁定。 -->
<template>
  <el-card shadow="never" v-loading="loading">
    <template #header>
      <div class="fb-head">
        <b>报名表</b>
        <span class="dim">志愿者申请时填写；意向场次由系统自动列出，不用出题</span>
        <el-button v-if="editable" type="primary" size="small" :loading="saving"
                   style="margin-left:auto" @click="save">保存报名表</el-button>
      </div>
    </template>
    <el-alert v-if="!editable" type="info" :closable="false" style="margin-bottom:10px"
              title="活动已公开，报名表不能再改——已经有人按这份表填了" />
    <ol v-if="!editable && questions.length" class="fb-readonly">
      <li v-for="q in questions" :key="q.id">
        <b>{{ q.title }}</b>
        <el-tag size="small" effect="plain" style="margin:0 6px">{{ KINDS[q.kind] }}</el-tag>
        <el-tag v-if="q.required" size="small" type="warning" effect="plain">必填</el-tag>
        <div v-if="q.options?.length" class="dim">选项：{{ q.options.join(' / ') }}</div>
        <div v-if="q.condQ" class="dim">{{ condText(q) }}</div>
      </li>
    </ol>
    <div v-for="(q, i) in questions" v-else :key="i" class="fb-row">
      <span class="fb-no">{{ i + 1 }}.</span>
      <el-select v-model="q.kind" style="width:120px" :disabled="!editable">
        <el-option v-for="(label, k) in KINDS" :key="k" :value="k" :label="label" />
      </el-select>
      <el-input v-model="q.title" placeholder="题目" maxlength="200"
                :disabled="!editable" class="fb-title" />
      <el-checkbox v-model="q.required" :disabled="!editable">必填</el-checkbox>
      <el-button-group v-if="editable" size="small">
        <el-button :disabled="i === 0" @click="move(i, -1)">上移</el-button>
        <el-button :disabled="i === questions.length - 1" @click="move(i, 1)">下移</el-button>
        <el-button type="danger" plain @click="questions.splice(i, 1)">删除</el-button>
      </el-button-group>
      <el-input v-if="q.kind === 'single' || q.kind === 'multi'" v-model="q.optionsText"
                type="textarea" :rows="2" placeholder="选项，一行一个（至少两个）"
                :disabled="!editable" class="fb-opts" />
      <!-- 显示条件：只能挂在前面某道选择题的某几个选项上 -->
      <div class="fb-cond">
        <span class="dim">显示条件</span>
        <el-select v-model="q.condQ" clearable placeholder="一直显示" style="width:220px"
                   :disabled="!editable" @change="clearCondValues(q)">
          <el-option v-for="c in conditionSources(i)" :key="c.id" :value="c.id"
                     :label="c.title" />
        </el-select>
        <el-select v-if="q.condQ" v-model="q.condValues" multiple collapse-tags
                   style="width:260px" placeholder="选中其中之一时才显示"
                   :disabled="!editable">
          <el-option v-for="o in optionsOf(q.condQ)" :key="o" :value="o" :label="o" />
        </el-select>
      </div>
    </div>
    <el-button v-if="editable" plain size="small" @click="add">添加题目</el-button>
    <div v-if="editable" class="dim" style="margin-top:6px">
      显示条件挂在前面某道选择题上；新加的题保存之后才能被后面的题挑成条件。
    </div>
    <el-empty v-else-if="!questions.length" description="没有设置题目" :image-size="40" />
  </el-card>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const props = defineProps({
  activityId: { type: String, required: true },
  editable: { type: Boolean, default: false },
})

const KINDS = { single: '单选', multi: '多选', text: '填空', file: '文件上传' }
const questions = ref([])
const loading = ref(false)
const saving = ref(false)

async function load() {
  loading.value = true
  try {
    const d = await api.get(`/api/staff/activities/${props.activityId}/form`)
    questions.value = d.questions.map(toRow)
  } finally { loading.value = false }
}
onMounted(load)

const toRow = q => ({
  ...q, optionsText: (q.options || []).join('\n'),
  condQ: q.show_if?.question_id || '', condValues: q.show_if?.values || [],
})

function add() {
  questions.value.push({ id: '', kind: 'single', title: '', optionsText: '',
                         required: true, condQ: '', condValues: [] })
}

// 能当条件的：排在这道题前面的单选/多选题。往后指会绕成环
function conditionSources(index) {
  return questions.value.slice(0, index)
    .filter(q => q.id && (q.kind === 'single' || q.kind === 'multi') && q.title.trim())
}
const optionsOf = qid => (questions.value.find(q => q.id === qid)?.optionsText || '')
  .split('\n').map(s => s.trim()).filter(Boolean)
const clearCondValues = q => { q.condValues = [] }
const condText = q => `仅当「${questions.value.find(x => x.id === q.condQ)?.title || ''}」`
  + `选了 ${q.condValues.join(' / ')} 时显示`
function move(i, delta) {
  const list = questions.value
  const [q] = list.splice(i, 1)
  list.splice(i + delta, 0, q)
}

async function save() {
  saving.value = true
  try {
    const body = questions.value.map(q => ({
      id: q.id || '', kind: q.kind, title: q.title, required: q.required,
      options: q.optionsText.split('\n').map(s => s.trim()).filter(Boolean),
      show_if: q.condQ ? { question_id: q.condQ, values: q.condValues } : {},
    }))
    const d = await api.put(`/api/staff/activities/${props.activityId}/form`,
                            { questions: body })
    questions.value = d.questions.map(toRow)
    ElMessage.success('报名表已保存')
  } finally { saving.value = false }
}
</script>

<style scoped>
.fb-head { display: flex; align-items: center; gap: 10px; }
.fb-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
          padding: 8px 0; border-bottom: 1px dashed #eceef1; }
.fb-no { width: 22px; color: #909399; }
.fb-title { flex: 1 1 240px; min-width: 0; }
.fb-opts { width: 100%; margin-left: 30px; }
.fb-readonly { margin: 0; padding-left: 20px; line-height: 2; }
.fb-cond { width: 100%; margin-left: 30px; display: flex; align-items: center;
           gap: 8px; flex-wrap: wrap; }
</style>
