<!-- 智能起草岗位：草稿放在可改的预览里，管理者改好、点「按草稿新建」才逐条调
     新建岗位的接口——校验、审计和手建的一样。一条岗位一张卡，各带各的服务
     时段与自动锁定。
     培训、彩排挂在整个活动上，等岗位都建好了再建，好把要来的岗位对上：草稿里
     勾的是预览里的那几条岗位（同名岗位分几个时段时也分得开），一条都不勾是全体。
     只新建、不碰已有的岗位。中途某一条被后端拦下就停在那儿：已建好的从预览里
     划掉，改好剩下的再点一次接着建。 -->
<template>
  <AiAssist ref="assist" title="智能起草岗位" :run="run"
            placeholder="把排班安排、立项书里的人员分工贴在这里，如：10 月 18 日 7:30-12:00，检录引导 6 人、颁奖礼仪 4 人（女生）；10 月 16 日晚 7 点全体彩排……"
            hint="根据材料起草岗位：一条岗位带自己的时段（可以多段）、名额与分工；培训、彩排挂到对应岗位上。增量新建，不改已有的岗位。">
    <template #preview>
      <el-empty v-if="!posts.length && !trainings.length" description="原文里没找到能用的岗位安排" />
      <div v-for="(p, pi) in posts" :key="p.key" class="ai-group">
        <div class="ai-head">
          <el-input v-model="p.name" placeholder="岗位名" style="flex:1 1 120px" />
          <el-input-number v-model="p.quota" :min="1" :max="999" style="width:120px" />
          <span class="dim">人</span>
          <el-button link type="danger" @click="posts.splice(pi, 1)">删掉</el-button>
        </div>
        <PeriodEditor v-model="p.periods" />
        <div class="ai-head">
          <span class="dim">开场前</span>
          <el-input-number v-model="p.lock_hours" :min="0" :max="72" style="width:120px" />
          <span class="dim">小时锁定名单（0 = 不自动锁定）</span>
        </div>
        <el-input v-model="p.service_task" type="textarea" :autosize="{ minRows: 1, maxRows: 4 }"
                  placeholder="服务任务（必填）：到岗做什么"
                  :class="{ missing: !p.service_task.trim() }" />
        <div v-if="extras(p)" class="dim pre">{{ extras(p) }}</div>
      </div>
      <!-- 培训、彩排：等岗位都建好再建，好把「哪些岗位要来」对上 -->
      <div v-for="(a, ti) in trainings" :key="'a' + ti" class="ai-group">
        <div class="ai-head">
          <el-select v-model="a.kind" style="width:88px">
            <el-option value="training" label="培训" />
            <el-option value="rehearsal" label="彩排" />
          </el-select>
          <el-input v-model="a.name" placeholder="名称" style="flex:1 1 120px" />
          <el-button link type="danger" @click="trainings.splice(ti, 1)">删掉</el-button>
        </div>
        <PeriodEditor v-model="a.periods" tip="培训、彩排的时间，报上下面勾选岗位的人都要来。" />
        <div class="dim">需参加的岗位（都不勾 = 全体）：</div>
        <el-checkbox-group v-model="a.keys" class="ai-picks">
          <el-checkbox v-for="x in pickable" :key="x.key" :value="x.key">{{ x.label }}</el-checkbox>
        </el-checkbox-group>
        <div class="dim pre">{{ attachedText(a) }}</div>
      </div>
    </template>
    <template #footer="{ close }">
      <el-button @click="close">先不用</el-button>
      <el-button type="primary" :disabled="!posts.length && !trainings.length"
                 :loading="creating" @click="create(close)">
        按草稿新建 {{ posts.length }} 条岗位{{
          trainings.length ? `、${trainings.length} 场培训/彩排` : '' }}
      </el-button>
    </template>
  </AiAssist>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { aiForm } from '../ai'
import AiAssist from './AiAssist.vue'
import PeriodEditor from './PeriodEditor.vue'

const props = defineProps({ activityId: { type: String, required: true } })
const emit = defineEmits(['created'])

const assist = ref(null)
// 可改的草稿：起草结果拷一份进来，预览里直接改它
const posts = ref([])          // 岗位，每条带 key（只在这份草稿里用）
const trainings = ref([])      // 培训/彩排，keys 是要来的岗位的 key
const done = ref({})  // vuecheck: script-only（已经建好的岗位：key → { id, name }，接着建培训时要用）
const creating = ref(false)

/** 后端把模型写的岗位序号换成了岗位在 posts 里的下标；进预览再换成 key：
 *  预览里删了、改了岗位，勾选照样对得上。 */
async function run(text, files) {
  const d = await api.post(`/api/staff/ai/activities/${props.activityId}/structure-draft`,
                           aiForm(text, files))
  const copy = JSON.parse(JSON.stringify(d))
  posts.value = copy.posts.map((p, i) => ({ ...p, key: `p${i}` }))
  trainings.value = copy.trainings.map(({ posts: idx, ...a }) => ({
    ...a, keys: idx.map(i => `p${i}`),
  }))
  done.value = {}
  return d
}

/** 时段的简写：「10-25 09:00」，几段的写「等 N 段」——勾选框里认得出是哪一条就够了 */
function brief(periods) {
  const first = periods?.[0]?.start_time
  if (!first) return '时段待填'
  const s = String(first).replace('T', ' ').slice(5, 16)
  return periods.length > 1 ? `${s} 等 ${periods.length} 段` : s
}
// 培训能勾的岗位：这一轮已经建好的，加上预览里还在的
const pickable = computed(() => [
  ...Object.entries(done.value).map(([key, x]) => ({ key, label: `${x.name}（已建）` })),
  ...posts.value.map(p => ({ key: p.key, label: `${p.name || '未命名'}（${brief(p.periods)}）` })),
])
const known = key => posts.value.some(p => p.key === key) || key in done.value

const tagText = tags => Object.entries(tags || {}).map(([k, v]) => `${k}：${v.join('、')}`)
  .join('；')

const ATTIRE = { issued: '统一发放', own: '志愿者自备' }
/** 预览里不单独给输入框的那些字段，写成一段话给人过目；建好后都能再改 */
function extras(p) {
  return [
    p.group_name && `岗位组：${p.group_name}`,
    p.location && `地点：${p.location}`,
    p.position_requirement && `岗位要求：${p.position_requirement}`,
    p.training_note && `培训：${p.training_note}`,
    p.needs_leader && `组长任务：${p.leader_task}`,
    p.attire !== 'none' && `志愿服装：${ATTIRE[p.attire]}（${p.attire_note}）`,
    p.pay_optional && `报酬方式：可选劳务（${p.pay_note}）`,
    tagText(p.requirement_tags) && `条件：${tagText(p.requirement_tags)}`,
  ].filter(Boolean).join('\n')
}

const HOURS = { fixed: '固定计时', checkin: '扫码计时', none: '不计时长' }
function attachedText(a) {
  return [
    a.keys.length && !a.keys.some(known) ? '勾的岗位都删掉了' : '',
    `时长：${HOURS[a.hours_mode]}`,
    a.location && `地点：${a.location}`,
    a.note && `备注：${a.note}`,
  ].filter(Boolean).join('\n')
}

/** 时段填全了没有；返回空串是没问题。 */
function badPeriods(list) {
  if (!list?.length) return '请至少填写一个服务时段'
  for (const p of list) {
    if (!p.start_time || !p.end_time) return '请把每个时段的起止时间填完整'
    if (new Date(p.end_time) <= new Date(p.start_time)) {
      return '时段的结束时间要晚于开始时间'
    }
  }
  return ''
}

/** 建之前先在前端过一遍：后端照样会查，但建到一半才被拦，前面那几个已经落库了 */
function problem() {
  for (const p of posts.value) {
    if (!p.name.trim()) return '有岗位没写名字'
    const bad = badPeriods(p.periods)
    if (bad) return `「${p.name}」：${bad}`
    if (!p.service_task.trim()) return `请填写岗位「${p.name}」的服务任务`
    if (p.needs_leader && !p.leader_task?.trim()) return `请给岗位「${p.name}」写组长任务`
  }
  for (const a of trainings.value) {
    if (!a.name.trim()) return '有培训/彩排没写名字'
    const bad = badPeriods(a.periods)
    if (bad) return `「${a.name}」：${bad}`
    // 一个都不剩就成了「全体都要来」，和原文的意思反了
    if (a.keys.length && !a.keys.some(known)) {
      return `「${a.name}」勾的岗位都删掉了：重新勾选，或者都不勾表示全体参加`
    }
  }
  return ''
}

async function create(close) {
  const err = problem()
  if (err) return ElMessage.warning(err)
  creating.value = true
  let made = 0
  let attached = 0
  try {
    while (posts.value.length) {
      const { name, key, ...rest } = posts.value[0]
      // 岗位自带时段：时段与自动锁定跟着这条岗位一起建
      const { id } = await api.post(
        `/api/staff/activities/${props.activityId}/posts`, { ...rest, name: name.trim() })
      done.value = { ...done.value, [key]: { id, name: name.trim() } }
      posts.value.shift()
      made += 1
    }
    while (trainings.value.length) {
      const { keys, ...a } = trainings.value[0]
      await api.post(`/api/staff/activities/${props.activityId}/shifts`, {
        ...a, name: a.name.trim(), location: a.location.trim(),
        applies_to_positions: keys.map(k => done.value[k]?.id).filter(Boolean),
      })
      trainings.value.shift()
      attached += 1
    }
    ElMessage.success(`已新建 ${made} 条岗位`
                      + (attached ? `、${attached} 场培训/彩排` : '') + '，请逐个核对')
    close()
    assist.value.reset()
  } catch {
    // 后端的提示 api 层已经弹过；建好的已从预览里划掉，改好剩下的再点一次
  } finally {
    creating.value = false
    if (made || attached) emit('created')
  }
}
</script>

<style scoped>
.ai-group { border: 1px solid #eceef1; border-radius: 4px; padding: 12px;
            margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.ai-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ai-head .el-button { margin-left: auto; }
.ai-picks { display: flex; flex-wrap: wrap; gap: 4px 16px; }
.missing :deep(.el-textarea__inner) { box-shadow: 0 0 0 1px var(--el-color-danger) inset; }
</style>
