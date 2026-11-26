<!-- 智能起草岗位：草稿放在可改的预览里，管理者改好、点「按草稿新建」才逐条调
     新建岗位的接口——校验、审计和手建的一样。岗位自带时段，时段相同的岗位在
     草稿里归成一组，一组里的每条岗位都用这一组的时段与自动锁定。
     培训、彩排挂在整个活动上，等它服务的那几条岗位建好了再建，好把要来的岗位
     对上；原文说「这一组的人都来」的，就圈上这一组建出来的全部岗位。
     只新建、不碰已有的岗位。中途某一条被后端拦下就停在那儿：已建好的从预览里
     划掉，改好剩下的再点一次接着建。 -->
<template>
  <AiAssist ref="assist" title="智能起草岗位" :run="run"
            placeholder="把排班安排、立项书里的人员分工贴在这里，如：10 月 18 日 7:30-12:00，检录引导 6 人、颁奖礼仪 4 人（女生）；10 月 16 日晚 7 点全体彩排……"
            hint="根据材料起草岗位：一条岗位带自己的服务时段（可以多段）、名额与分工，时段相同的岗位归成一组；培训、彩排挂到对应岗位上。增量新建，不改已有的岗位。">
    <template #preview>
      <el-empty v-if="!draft.length" description="原文里没找到能用的岗位安排" />
      <div v-for="(g, gi) in draft" :key="gi" class="ai-group">
        <div class="ai-head">
          <b style="flex:1 1 160px">{{ g.name }}</b>
          <el-button link type="danger" @click="draft.splice(gi, 1)">删掉这一组</el-button>
        </div>
        <PeriodEditor v-model="g.periods" :tip="GROUP_TIP" />
        <div class="ai-head">
          <span class="dim">开场前</span>
          <el-input-number v-model="g.lock_hours" :min="0" :max="72" style="width:120px" />
          <span class="dim">小时锁定名单（0 = 不自动锁定）</span>
        </div>
        <div v-for="(p, pi) in g.positions" :key="p.key" class="ai-pos">
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
        <div v-if="!g.positions.length" class="dim">这一组没有岗位，不会新建任何东西</div>
        <!-- 培训、彩排：等这一组的岗位建好再建，好把「哪些岗位要来」对上 -->
        <div v-for="(a, ti) in g.attached" :key="'a' + ti" class="ai-pos">
          <div class="ai-head">
            <el-select v-model="a.kind" style="width:88px">
              <el-option value="training" label="培训" />
              <el-option value="rehearsal" label="彩排" />
            </el-select>
            <el-input v-model="a.name" placeholder="名称" style="flex:1 1 120px" />
            <el-button link type="danger" @click="g.attached.splice(ti, 1)">删掉</el-button>
          </div>
          <PeriodEditor v-model="a.periods" />
          <div class="dim pre">{{ attachedText(g, a) }}</div>
        </div>
      </div>
    </template>
    <template #footer="{ close }">
      <el-button @click="close">先不用</el-button>
      <el-button type="primary" :disabled="!positionCount && !attachedCount"
                 :loading="creating" @click="create(close)">
        按草稿新建 {{ positionCount }} 条岗位{{
          attachedCount ? `、${attachedCount} 场培训/彩排` : '' }}
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
const draft = ref([])          // 可改的草稿：起草结果拷一份进来，预览里直接改它
const creating = ref(false)
// 一组里的岗位共用这一组的时段：填的时候就要说清楚「每段都要来」
const GROUP_TIP = '这一组里的每条岗位都用下面的时段，'
  + '报名的人每个时段都要来；时段不一样的岗位分在别的组里。'
const positionCount = computed(() => draft.value.reduce((n, g) => n + g.positions.length, 0))
const attachedCount = computed(() => draft.value.reduce((n, g) => n + g.attached.length, 0))

/** 培训/彩排「哪些岗位要来」起草时写的是岗位名，进预览就换成岗位的 key：
 *  预览里改了岗位名照样对得上，删掉的岗位自然就不算了。 */
async function run(text, files) {
  const d = await api.post(`/api/staff/ai/activities/${props.activityId}/structure-draft`,
                           aiForm(text, files))
  draft.value = JSON.parse(JSON.stringify(d.shifts)).map((g, gi) => {
    g.positions.forEach((p, pi) => { p.key = `${gi}-${pi}` })
    g.attached = g.attached.map(({ positions, ...a }) => ({
      ...a, keys: positions.map(n => g.positions.find(p => p.name === n)?.key).filter(Boolean),
    }))
    g.done = {}     // 已经建好的岗位：key → { id, name }
    return g
  })
  return d
}

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

const posName = (g, key) => g.positions.find(p => p.key === key)?.name || g.done[key]?.name
const HOURS = { fixed: '固定计时', checkin: '扫码计时', none: '不计时长' }
function attachedText(g, a) {
  const names = a.keys.map(k => posName(g, k)).filter(Boolean)
  return [
    `需参加：${names.length ? names.join('、')
      : a.keys.length ? '（岗位都删掉了）' : '这一组的全部岗位'}`,
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
  for (const g of draft.value) {
    const bad = badPeriods(g.periods)
    if (bad) return `「${g.name}」：${bad}`
    for (const p of g.positions) {
      if (!p.name.trim()) return `「${g.name}」里有岗位没写名字`
      if (!p.service_task.trim()) return `请填写岗位「${p.name}」的服务任务`
      if (p.needs_leader && !p.leader_task?.trim()) return `请给岗位「${p.name}」写组长任务`
    }
    for (const a of g.attached) {
      if (!a.name.trim()) return `「${g.name}」里有培训/彩排没写名字`
      const badAttached = badPeriods(a.periods)
      if (badAttached) return `「${a.name}」：${badAttached}`
      // 一个都不剩就成了「全场次都要来」，和原文的意思反了
      if (a.keys.length && !a.keys.some(k => posName(g, k))) {
        return `「${a.name}」要参加的岗位都删掉了：删掉这一场，或者建好后再手动设置`
      }
    }
  }
  return ''
}

async function create(close) {
  const err = problem()
  if (err) return ElMessage.warning(err)
  creating.value = true
  let positions = 0
  let attached = 0
  try {
    while (draft.value.length) {
      const g = draft.value[0]
      while (g.positions.length) {
        const { name, key, ...rest } = g.positions[0]
        // 岗位自带时段：这一组的时段与自动锁定跟着每条岗位一起建
        const { id } = await api.post(
          `/api/staff/activities/${props.activityId}/posts`,
          { ...rest, name: name.trim(), periods: g.periods,
            lock_hours: g.lock_hours })
        g.done[key] = { id, name: name.trim() }
        g.positions.shift()
        positions += 1
      }
      while (g.attached.length) {
        const { keys, ...a } = g.attached[0]
        // 圈空了在活动一级是「全体」，原文的意思是「这一组的人」：圈上这一组的全部岗位
        const ids = (keys.length ? keys.map(k => g.done[k]?.id)
                                 : Object.values(g.done).map(x => x.id)).filter(Boolean)
        await api.post(`/api/staff/activities/${props.activityId}/shifts`, {
          ...a, name: a.name.trim(), location: a.location.trim(), applies_to_positions: ids,
        })
        g.attached.shift()
        attached += 1
      }
      draft.value.shift()
    }
    ElMessage.success(`已新建 ${positions} 条岗位`
                      + (attached ? `、${attached} 场培训/彩排` : '') + '，请逐个核对')
    close()
    assist.value.reset()
  } catch {
    // 后端的提示 api 层已经弹过；建好的已从预览里划掉，改好剩下的再点一次
  } finally {
    creating.value = false
    if (positions || attached) emit('created')
  }
}
</script>

<style scoped>
.ai-group { border: 1px solid #eceef1; border-radius: 4px; padding: 12px;
            margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.ai-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ai-head .el-button { margin-left: auto; }
.ai-pos { border-top: 1px dashed #eceef1; padding-top: 8px;
          display: flex; flex-direction: column; gap: 8px; }
.two-col { display: flex; gap: 8px; flex-wrap: wrap; }
.two-col > * { flex: 1 1 180px; min-width: 0; }
.missing :deep(.el-textarea__inner) { box-shadow: 0 0 0 1px var(--el-color-danger) inset; }
</style>
