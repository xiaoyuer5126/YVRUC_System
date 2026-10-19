<!-- 志愿北京上的活动名称，一行一个，可以填好几个（分几场建的就填几个）。
     提交时长审核、审核页签里先存、被外联退回后重提，三处共用。
     这里只管录入；去空白、去重与「至少一个」由后端把关，前端只提前提示。 -->
<template>
  <div class="vb-names">
    <div v-for="(_, i) in list" :key="i" class="vb-row">
      <el-input v-model="list[i]" maxlength="200" :disabled="disabled"
                :placeholder="i === 0 ? '志愿北京上的活动名称（全称）' : '再填一个名称'"
                @update:model-value="emitList" />
      <el-button v-if="list.length > 1 && !disabled" link type="danger"
                 @click="remove(i)">删除</el-button>
    </div>
    <el-button v-if="!disabled && list.length < MAX" link type="primary"
               @click="add">+ 再加一个名称</el-button>
    <div v-if="!disabled" class="dim">
      至少填 1 个，最多 {{ MAX }} 个；每个 2～200 字，写志愿北京上的全称。
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const MAX = 20
const list = ref([''])

watch(() => props.modelValue, v => {
  const cur = list.value.map(x => x.trim()).filter(Boolean)
  const next = (v || []).map(x => String(x))
  // 父组件回写的就是刚才发出去的那份：别打断正在输入的空行
  if (JSON.stringify(cur) === JSON.stringify(next)) return
  list.value = next.length ? [...next] : ['']
}, { immediate: true })

function emitList() {
  emit('update:modelValue', list.value.map(x => x.trim()).filter(Boolean))
}
function add() { list.value.push('') }
function remove(i) {
  list.value.splice(i, 1)
  emitList()
}
</script>

<style scoped>
.vb-names { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; width: 100%; }
.vb-row { display: flex; align-items: center; gap: 8px; align-self: stretch; }
</style>
