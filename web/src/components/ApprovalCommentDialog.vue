<!-- 审批意见对话框：所有「通过 / 驳回」都走这一个——活动立项、时长锁定/解锁、
     外联确认志愿北京。通过时意见选填，驳回（退回）时必填、至少 2 个字；
     意见随通知发给申请人，也留在审核页签的历史里。

     用法：<ApprovalCommentDialog v-model="show" :approve="true" title="…"
             subject="活动名" audience="发起方" :submit="async comment => …" />
     submit 抛错就不关（接口报的错 api 层已经弹过了），成功才关。

     快捷模板：templates 传一组常用语（字符串），点一下填进输入框；
     通过与驳回可以各传一组（approveTemplates / rejectTemplates）。
     常用语本身暂时不做，调用方都传空数组，这里留好口子。 -->
<template>
  <el-dialog :model-value="modelValue" :title="title || (approve ? approveLabel : rejectLabel)"
             width="480" @update:model-value="v => emit('update:modelValue', v)">
    <p v-if="subject" class="dim acd-subject">{{ subject }}</p>
    <slot />
    <div v-if="shownTemplates.length" class="acd-templates">
      <span class="dim">常用语：</span>
      <el-tag v-for="t in shownTemplates" :key="t" class="acd-tpl" effect="plain"
              @click="useTemplate(t)">{{ t }}</el-tag>
    </div>
    <el-input v-model="comment" type="textarea" :rows="3" maxlength="500" show-word-limit
              :placeholder="approve ? `${approveLabel}意见（选填），会发给${audience}`
                                    : `请写明${rejectLabel}理由（必填），会发给${audience}`" />
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button :type="approve ? 'success' : 'danger'" :loading="busy" @click="confirm">
        确认{{ approve ? approveLabel : rejectLabel }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  approve: { type: Boolean, default: true },       // true 通过 / false 驳回（退回）
  title: { type: String, default: '' },
  subject: { type: String, default: '' },         // 批的是哪一条（活动名等）
  audience: { type: String, default: '申请人' },   // 意见发给谁
  approveLabel: { type: String, default: '通过' },
  rejectLabel: { type: String, default: '驳回' },
  templates: { type: Array, default: () => [] },
  approveTemplates: { type: Array, default: () => [] },
  rejectTemplates: { type: Array, default: () => [] },
  submit: { type: Function, required: true },     // async (comment) => void
})
const emit = defineEmits(['update:modelValue'])
const comment = ref('')
const busy = ref(false)
const MIN_REASON = 2

// 每次打开都是一张白纸：上一条的意见不能带到下一条上
watch(() => props.modelValue, v => { if (v) { comment.value = ''; busy.value = false } })

const shownTemplates = computed(() => [
  ...props.templates,
  ...(props.approve ? props.approveTemplates : props.rejectTemplates)])

function useTemplate(t) {
  const cur = comment.value.trim()
  comment.value = cur ? `${cur}；${t}` : t
}

async function confirm() {
  const text = comment.value.trim()
  if (!props.approve && text.length < MIN_REASON) {
    return ElMessage.warning(`${props.rejectLabel}必须写明理由（至少 ${MIN_REASON} 个字）`)
  }
  busy.value = true
  try {
    await props.submit(text)
    emit('update:modelValue', false)
  } catch { /* 接口的错 api 层已经提示过，留着对话框让人改 */ } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.acd-subject { margin: 0 0 8px; }
.acd-templates { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px; }
.acd-tpl { cursor: pointer; }
</style>
