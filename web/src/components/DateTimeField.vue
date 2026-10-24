<!-- 选日期与时间：月历 + 时:分，不要秒。所有选时间的地方都用它。 -->
<template>
  <!-- 窄屏交给系统自带的选择器：手机上的滚轮比弹层月历好点，
       月历面板在 390px 宽的屏幕上也露不全 -->
  <!-- 系统选择器空着时不显示 placeholder（iOS 上就是一个空框），
       不叠一层提示字的话，人看不出这里要填 -->
  <label v-if="narrow" class="dt-wrap">
    <!-- 空着时把系统自己画的「年/月/日 --:--」藏掉，免得和提示字叠在一起 -->
    <input type="datetime-local" class="dt-native" :class="{ empty: !nativeValue }"
           :value="nativeValue" @change="onNative" />
    <span v-if="!nativeValue" class="dt-hint">{{ placeholder }}</span>
  </label>
  <el-date-picker v-else type="datetime" style="width:100%"
                  :model-value="modelValue || ''" :placeholder="placeholder"
                  format="YYYY-MM-DD HH:mm" time-format="HH:mm"
                  :default-time="defaultTime || undefined"
                  @update:model-value="pick" />
</template>

<script setup>
import { computed } from 'vue'
import { useNarrow } from '../composables/useNarrow'

const props = defineProps({
  modelValue: { type: [Date, String], default: '' },
  placeholder: { type: String, default: '选择日期与时间' },
  // 只点日期不点时刻时补哪个时刻：开始时间给 DAY_START，截止时间给 DAY_END。
  // 不补就别传——把 null 原样递给 Element，点日期时它算不出时刻，值会变成 NaN
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
.dt-wrap { position: relative; display: block; width: 100%; }
.dt-hint {
  position: absolute; left: 12px; top: 0; line-height: 32px;
  color: var(--el-text-color-placeholder); pointer-events: none;
}
.dt-native {
  width: 100%; box-sizing: border-box; height: 32px; padding: 0 11px;
  border: 1px solid var(--el-border-color); border-radius: 4px;
  font: inherit; color: var(--el-text-color-regular); background: #fff;
}
.dt-native.empty { color: transparent; }
.dt-native:focus { outline: none; border-color: var(--el-color-primary); }
</style>
