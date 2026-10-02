<!-- 智能起草场次与岗位：草稿放在可改的预览里，管理者改好、点「按草稿新建」才
     逐个调新建场次 / 新建岗位的接口——校验、审计和手建的一样。
     只新建、不碰已有的场次岗位。中途某一个被后端拦下就停在那儿：已建好的从
     预览里划掉，改好剩下的再点一次接着建。 -->
<template>
  <AiAssist ref="assist" title="智能起草场次岗位" :run="run"
            placeholder="把排班安排、立项书里的人员分工贴在这里，如：10 月 18 日 7:30-12:00，检录引导 6 人、颁奖礼仪 4 人（女生）……"
            hint="按原文起草场次（一段服务时间）和岗位（这段时间里的分工与名额）。只会新建，不改已有的场次岗位；培训、彩排建好后再手动加。">
    <template #preview>
      <el-empty v-if="!draft.length" description="原文里没找到能用的场次安排" />
      <div v-for="(g, gi) in draft" :key="gi" class="ai-shift">
        <div class="ai-head">
          <el-input v-model="g.name" placeholder="场次名" style="flex:1 1 160px" />
          <el-button link type="danger" @click="draft.splice(gi, 1)">删掉这个场次</el-button>
        </div>
        <div class="two-col">
          <DateTimeField v-model="g.start_time" placeholder="开始时间"
                         :default-time="DAY_START" />
          <DateTimeField v-model="g.end_time" placeholder="结束时间"
                         :default-time="DAY_END" />
        </div>
        <el-input v-if="g.location" v-model="g.location" placeholder="场次地点（空 = 活动地点）" />
        <div v-for="(p, pi) in g.positions" :key="pi" class="ai-pos">
          <div class="ai-head">
            <el-input v-model="p.name" placeholder="岗位名" style="flex:1 1 120px" />
            <el-input-number v-model="p.quota" :min="1" :max="999" style="width:120px" />
            <span class="dim">人</span>
            <el-button link type="danger" @click="g.positions.splice(pi, 1)">删掉</el-button>
          </div>
          <el-input v-model="p.service_task" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }"
                    placeholder="服务任务（必填）：到岗做什么"
                    :class="{ missing: !p.service_task.trim() }" />
          <div v-if="extras(p)" class="dim pre">{{ extras(p) }}</div>
        </div>
        <div v-if="!g.positions.length" class="dim">这个场次没有岗位，建好后再手动加</div>
      </div>
    </template>
    <template #footer="{ close }">
      <el-button @click="close">先不用</el-button>
      <el-button type="primary" :disabled="!draft.length" :loading="creating"
                 @click="create(close)">
        按草稿新建 {{ draft.length }} 个场次、{{ positionCount }} 个岗位
      </el-button>
    </template>
  </AiAssist>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { DAY_END, DAY_START } from '../store'
import AiAssist from './AiAssist.vue'
import DateTimeField from './DateTimeField.vue'

const props = defineProps({ activityId: { type: String, required: true } })
const emit = defineEmits(['created'])

const assist = ref(null)
const draft = ref([])          // 可改的草稿：起草结果拷一份进来，预览里直接改它
const creating = ref(false)
const positionCount = computed(() => draft.value.reduce((n, g) => n + g.positions.length, 0))

async function run(text) {
  const d = await api.post(`/api/staff/ai/activities/${props.activityId}/structure-draft`,
                           { text })
  draft.value = JSON.parse(JSON.stringify(d.shifts))
  return d
}

const ATTIRE = { issued: '统一发放', own: '志愿者自备' }
/** 预览里不单独给输入框的那些字段，写成一段话给人过目；建好后都能再改 */
function extras(p) {
  const tags = Object.entries(p.requirement_tags || {}).map(([k, v]) => `${k}：${v.join('、')}`)
  return [
    p.group_name && `岗位组：${p.group_name}`,
    p.location && `地点：${p.location}`,
    p.position_requirement && `岗位要求：${p.position_requirement}`,
    p.training_note && `培训：${p.training_note}`,
    p.needs_leader && '需要组长',
    p.attire !== 'none' && `志愿服装：${ATTIRE[p.attire]}（${p.attire_note}）`,
    tags.length && `条件：${tags.join('；')}`,
  ].filter(Boolean).join('\n')
}

/** 建之前先在前端过一遍：后端照样会查，但建到一半才被拦，前面那几个已经落库了 */
function problem() {
  for (const g of draft.value) {
    if (!g.name.trim()) return '有场次没写名字'
    if (!g.start_time || !g.end_time) return `请填写「${g.name}」的开始和结束时间`
    if (new Date(g.end_time) <= new Date(g.start_time)) return `「${g.name}」的结束时间要晚于开始时间`
    for (const p of g.positions) {
      if (!p.name.trim()) return `「${g.name}」里有岗位没写名字`
      if (!p.service_task.trim()) return `请填写岗位「${p.name}」的服务任务`
    }
  }
  return ''
}

async function create(close) {
  const err = problem()
  if (err) return ElMessage.warning(err)
  creating.value = true
  let shifts = 0
  let positions = 0
  try {
    while (draft.value.length) {
      const g = draft.value[0]
      // 场次建过了（上一次停在它的岗位上）就跳过，接着建剩下的岗位
      if (!g.createdId) {
        g.createdId = (await api.post(`/api/staff/activities/${props.activityId}/shifts`, {
          name: g.name.trim(), start_time: g.start_time, end_time: g.end_time,
          mode: 'pick', location: (g.location || '').trim(),
        })).id
        shifts += 1
      }
      while (g.positions.length) {
        const { name, ...rest } = g.positions[0]
        await api.post(`/api/staff/shifts/${g.createdId}/positions`, { ...rest, name: name.trim() })
        g.positions.shift()
        positions += 1
      }
      draft.value.shift()
    }
    ElMessage.success(`已新建 ${shifts} 个场次、${positions} 个岗位，请逐个核对`)
    close()
    assist.value.reset()
  } catch {
    // 后端的提示 api 层已经弹过；建好的已从预览里划掉，改好剩下的再点一次
  } finally {
    creating.value = false
    if (shifts || positions) emit('created')
  }
}
</script>

<style scoped>
.ai-shift { border: 1px solid #eceef1; border-radius: 12px; padding: 12px;
            margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.ai-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ai-head .el-button { margin-left: auto; }
.ai-pos { border-top: 1px dashed #eceef1; padding-top: 8px;
          display: flex; flex-direction: column; gap: 8px; }
.two-col { display: flex; gap: 8px; flex-wrap: wrap; }
.two-col > * { flex: 1 1 180px; min-width: 0; }
.missing :deep(.el-textarea__inner) { box-shadow: 0 0 0 1px var(--el-color-danger) inset; }
</style>
