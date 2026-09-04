<!-- 选日期与时间：月历 + 时:分，不要秒。所有选时间的地方都用它。 -->
<template>
  <!-- 窄屏交给系统自带的选择器：手机上的滚轮比弹层月历好点，
       月历面板在 390px 宽的屏幕上也露不全 -->
  <input v-if="narrow" type="datetime-local" class="dt-native"
         :value="nativeValue" @change="onNative" />
  <el-date-picker v-else type="datetime" style="width:100%"
                  :model-value="modelValue || ''" :placeholder="placeholder"
                  format="YYYY-MM-DD HH:mm" time-format="HH:mm"
                  :default-time="defaultTime"
                  @update:model-value="pick" />
</template>

<script setup>
import { computed } from 'vue'
import { useNarrow } from '../composables/useNarrow'

const props = defineProps({
  modelValue: { type: [Date, String], default: '' },
  placeholder: { type: String, default: '选择日期与时间' },
  // 只点日期不点时刻时补哪个时刻：开始时间给 DAY_START，截止时间给 DAY_END
  defaultTime: { type: Date, default: null },
})
const emit = defineEmits(['update:modelValue', 'change'])
const narrow = useNarrow()

const pad = n => String(n).padStart(2, '0')
const asDate = v => (v instanceof Date
  ? v
  : (v ? new Date(String(v).replace(' ', 'T')) : null))

// <input type="datetime-local"> 只认本地时间的 YYYY-MM-DDTHH:mm
const nativeValue = computed(() => {
  const d = asDate(props.modelValue)
  if (!d || Number.isNaN(d.getTime())) return ''
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
       + `T${pad(d.getHours())}:${pad(d.getMinutes())}`
})

function pick(v) {
  emit('update:modelValue', v || '')
  emit('change', v || '')
}
function onNative(e) {
  pick(e.target.value ? new Date(e.target.value) : '')
}
</script>

<style scoped>
.dt-native {
  width: 100%; box-sizing: border-box; height: 32px; padding: 0 11px;
  border: 1px solid var(--el-border-color); border-radius: 4px;
  font: inherit; color: var(--el-text-color-regular); background: #fff;
}
.dt-native:focus { outline: none; border-color: var(--el-color-primary); }
</style>
