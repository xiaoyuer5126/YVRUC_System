<!-- 窄屏下替代表格的卡片列表。
     表格在手机上要么横向滚动、要么把字挤成一列，两种都难用；
     卡片把「一行」竖起来，标题行放最要紧的两三项，其余按标签铺开。

     selectable 打开后卡片左上角出复选框，抛的事件与 el-table 同名同形
     （selection-change + 行数组），调用方两边共用一个处理函数：
     手机上没有多选，批量签到、批量确认时长这些功能就只剩桌面能用。
     与 el-table 一样，selectable 也可以传一个 (row) => 布尔 的函数，
     让某些行勾不上（比如已经处理过的申请）；全选只覆盖能勾的行。 -->
<template>
  <div v-if="rows.length" class="cards">
    <div v-if="pickableRows.length" class="pick-bar">
      <el-checkbox :model-value="allPicked" :indeterminate="somePicked"
                   @change="toggleAll">{{ t('全选') }}</el-checkbox>
      <span class="dim">{{ t('已选 {0} / {1}', [picked.length, pickableRows.length]) }}</span>
    </div>
    <div v-for="(row, i) in rows" :key="keyOf(row) ?? i" class="card"
         :class="{ picked: isPicked(row) }">
      <div class="card-head">
        <el-checkbox v-if="pickable(row)" :model-value="isPicked(row)"
                     @change="toggle(row)" />
        <div class="card-title">
          <slot name="title" :row="row">{{ valueOf(row, titleKey) }}</slot>
        </div>
        <div class="card-extra"><slot name="extra" :row="row" /></div>
      </div>

      <div v-if="fields.length" class="card-fields">
        <div v-for="f in visibleFields(row)" :key="f.key" class="field">
          <span class="field-label">{{ f.label }}</span>
          <span class="field-value">
            <slot :name="`field-${f.key}`" :row="row" :value="valueOf(row, f.key)">
              {{ format(f, valueOf(row, f.key)) }}
            </slot>
          </span>
        </div>
      </div>

      <div v-if="$slots.body" class="card-body"><slot name="body" :row="row" /></div>
      <div v-if="$slots.actions" class="card-actions"><slot name="actions" :row="row" /></div>
    </div>
  </div>
  <el-empty v-else :description="empty || t('暂无数据')" />
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { t } from '../i18n'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  /** [{ key, label, format?, hideEmpty? }]；key 支持 'user.name' 这种路径 */
  fields: { type: Array, default: () => [] },
  titleKey: { type: String, default: 'name' },
  /** 行标识，多选靠它认人；与 fields 一样支持 'user.id' 这种路径 */
  rowKey: { type: String, default: 'id' },
  empty: { type: String, default: '' },
  /** 布尔：整表可选；函数：按行判 (row) => 布尔，与 el-table 同义 */
  selectable: { type: [Boolean, Function], default: false },
})
const emit = defineEmits(['selection-change'])

// 只存 key，不存行对象：刷新之后行是新对象，存对象会把勾选全弄丢
const keys = ref([])   // vuecheck: script-only，模板只通过 isPicked/picked 读它
const keyOf = row => valueOf(row, props.rowKey)
// selectable 是函数时按行判，是布尔时整表统一——与 el-table 的 selectable 同义
const pickable = row => (typeof props.selectable === 'function'
  ? !!props.selectable(row) : props.selectable)
const pickableRows = computed(() => props.rows.filter(pickable))
const picked = computed(() => props.rows.filter(r => keys.value.includes(keyOf(r))))
const isPicked = row => keys.value.includes(keyOf(row))
const allPicked = computed(
  () => pickableRows.value.length > 0 && picked.value.length === pickableRows.value.length)
const somePicked = computed(
  () => picked.value.length > 0 && !allPicked.value)

function toggle(row) {
  const k = keyOf(row)
  keys.value = isPicked(row) ? keys.value.filter(x => x !== k) : [...keys.value, k]
}
function toggleAll(on) {
  keys.value = on ? pickableRows.value.map(keyOf) : []
}
// 行没了就把它的勾选一并丢掉，免得批量操作带上一个已经不在列表里的人
watch(() => props.rows, rows => {
  // row-key 取不到值时每一行都是 undefined，勾一个等于勾全部，而且看不出异样
  // （「已选 8 / 8」像是正常的全选）。这种接错必须当场喊出来。
  if (props.selectable && rows.some(r => keyOf(r) === undefined || keyOf(r) === null)) {
    console.error(`DataCards：row-key「${props.rowKey}」在这批数据上取不到值，多选会失灵`)
  }
  const live = new Set(rows.map(keyOf))
  const kept = keys.value.filter(k => live.has(k))
  if (kept.length !== keys.value.length) keys.value = kept
}, { immediate: true })
// 勾选的行真变了才抛。调用方常写 :rows="data?.items || []"，数据没到之前每次
// 渲染都是一个新的空数组；照数组身份抛的话，父组件收到后改状态、重渲染，又是
// 一个新的空数组——在微任务里转不出来，接口的响应永远落不了地，页面直接卡死。
// 刷新后行换成了新对象（勾的还是那几个人）要照抛：父组件手里得是新的行
watch(picked, (rows, old) => {
  if (old && rows.length === old.length && rows.every((r, i) => r === old[i])) return
  emit('selection-change', rows)
})

function valueOf(row, key) {
  return key.split('.').reduce((o, k) => (o == null ? o : o[k]), row)
}

function format(field, value) {
  if (field.format) return field.format(value)
  return value === null || value === undefined || value === '' ? '—' : value
}

function visibleFields(row) {
  return props.fields.filter(f => {
    if (!f.hideEmpty) return true
    const v = valueOf(row, f.key)
    return v !== null && v !== undefined && v !== '' && v !== false
  })
}
</script>

<style scoped>
.cards { display: flex; flex-direction: column; gap: 12px; }
.card {
  background: #fff; border: 1px solid #eceef1; border-radius: 4px;
  padding: 12px 14px;
}
.card.picked { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.pick-bar { display: flex; align-items: center; gap: 12px; padding: 0 2px; }
.card-head {
  display: flex; align-items: flex-start; gap: 8px; justify-content: space-between;
}
/* flex:1 是给多选那一版留的：加了复选框之后卡片头有三样东西，
   标题不占满剩余宽度就会被 space-between 推到中间去 */
.card-title { flex: 1; min-width: 0; font-weight: 600; font-size: 14.5px; line-height: 1.45; }
.card-extra { flex-shrink: 0; display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.card-fields {
  margin-top: 8px; display: grid; grid-template-columns: auto 1fr;
  gap: 4px 12px; font-size: 13px;
}
.field { display: contents; }
.field-label { color: #909399; white-space: nowrap; }
.field-value { color: #303133; word-break: break-word; }
.card-body { margin-top: 8px; }
.card-actions {
  margin-top: 12px; padding-top: 10px; border-top: 1px dashed #eceef1;
  display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
}
</style>
