<!-- 智能起草活动基本信息：草稿列出来给人看，点「填进新建表单」才打开新建活动
     并把有的字段填上——表单照常要管理者核对、补齐（联络群二维码智能起草给不了）、保存。 -->
<template>
  <AiAssist ref="assist" title="智能起草活动信息" :run="run"
            placeholder="把活动通知、立项书正文或一段描述贴在这里"
            hint="贴进活动的原始材料，智能起草新建活动表单里的基本信息；场次和岗位在建好活动后，到「场次岗位」页签里再起草。">
    <template #preview="{ result }">
      <el-empty v-if="!Object.keys(result.draft).length" description="原文里没找到能用的活动信息" />
      <el-descriptions v-else :column="1" border size="small">
        <el-descriptions-item v-for="row in rows(result.draft)" :key="row.label" :label="row.label">
          <span class="pre">{{ row.value }}</span>
        </el-descriptions-item>
      </el-descriptions>
    </template>
    <template #footer="{ result, close }">
      <el-button @click="close">先不用</el-button>
      <el-button type="primary" :disabled="!Object.keys(result.draft).length"
                 @click="fill(result.draft, close)">填进新建表单</el-button>
    </template>
  </AiAssist>
</template>

<script setup>
import { ref } from 'vue'
import { api } from '../api'
import AiAssist from './AiAssist.vue'

const emit = defineEmits(['fill'])
const assist = ref(null)

const MODES = { instant: '即选即得', screened: '报名后筛选' }
const LABELS = [
  ['title', '活动名称'], ['organizer', '组织单位'], ['recruit_mode', '活动类型', v => MODES[v]],
  ['location', '活动地点'],
  ['contacts', '联系人', v => v.map(c => `${c.name} ${c.phone}`.trim()).join('\n')],
  ['intro', '活动简介'], ['volunteer_requirement', '志愿者要求'],
  ['volunteer_benefit', '志愿者保障'],
  ['recruit_campuses', '招募校区', v => v.join('、')],
  ['recruit_stages', '招募学段', v => v.join('、')],
  ['reg_start', '报名开始', v => v.replace('T', ' ')],
  ['reg_end', '报名截止', v => v.replace('T', ' ')],
]
function rows(draft) {
  return LABELS.filter(([k]) => draft[k] !== undefined)
    .map(([k, label, fmt]) => ({ label, value: fmt ? fmt(draft[k]) : draft[k] }))
}

function run(text) {
  return api.post('/api/staff/ai/activity-draft', { text })
}

function fill(draft, close) {
  emit('fill', draft)
  close()
  assist.value.reset()
}
</script>

<style scoped>
/* 标签列不换行：值里常有几百字的简介，自动布局会把「活动名称」挤成一字一行 */
:deep(.el-descriptions__label) { white-space: nowrap; }
</style>
