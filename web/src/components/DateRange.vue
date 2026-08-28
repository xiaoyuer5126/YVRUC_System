<template>
  <!-- 窄屏：拆成两个单面板选择器。范围面板是双联的，约 646px 宽，
       手机上左右两半都露不全，点不到结束时间。 -->
  <div v-if="narrow" class="stack">
    <el-date-picker :model-value="value[0]" type="datetime" style="width:100%"
                    :placeholder="startPlaceholder" @update:model-value="setStart" />
    <el-date-picker :model-value="value[1]" type="datetime" style="width:100%"
                    :placeholder="endPlaceholder" :default-time="DAY_END"
                    @update:model-value="setEnd" />
  </div>
  <el-date-picker v-else :model-value="value" type="datetimerange" style="width:100%"
                  :default-time="RANGE_DEFAULT_TIME"
                  :start-placeholder="startPlaceholder" :end-placeholder="endPlaceholder"
                  @update:model-value="setRange" />
</template>

<script setup>
import { computed } from 'vue'
import { DAY_END, RANGE_DEFAULT_TIME } from '../store'
import { useNarrow } from '../composables/useNarrow'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  startPlaceholder: { type: String, default: '开始时间' },
  endPlaceholder: { type: String, default: '结束时间' },
})
const emit = defineEmits(['update:modelValue'])
const narrow = useNarrow()
const value = computed(() => props.modelValue || [])

// 两个单选各改各的一头，拼回同一个数组交出去
function set(i, v) {
  const next = [value.value[0] || '', value.value[1] || '']
  next[i] = v || ''
  emit('update:modelValue', next[0] || next[1] ? next : [])
}
const setStart = v => set(0, v)
const setEnd = v => set(1, v)
const setRange = v => emit('update:modelValue', v || [])
</script>

<style scoped>
.stack { display: flex; flex-direction: column; gap: 8px; width: 100%; }
</style>
