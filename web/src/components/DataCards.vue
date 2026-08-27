<!-- 窄屏下替代表格的卡片列表。
     表格在手机上要么横向滚动、要么把字挤成一列，两种都难用；
     卡片把「一行」竖起来，标题行放最要紧的两三项，其余按标签铺开。 -->
<template>
  <div v-if="rows.length" class="cards">
    <div v-for="(row, i) in rows" :key="row[rowKey] || i" class="card">
      <div class="card-head">
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
  <el-empty v-else :description="empty" :image-size="60" />
</template>

<script setup>
const props = defineProps({
  rows: { type: Array, default: () => [] },
  /** [{ key, label, format?, hideEmpty? }]；key 支持 'user.name' 这种路径 */
  fields: { type: Array, default: () => [] },
  titleKey: { type: String, default: 'name' },
  rowKey: { type: String, default: 'id' },
  empty: { type: String, default: '暂无数据' },
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
.cards { display: flex; flex-direction: column; gap: 10px; }
.card {
  background: #fff; border: 1px solid #eceef1; border-radius: 10px;
  padding: 12px 14px;
}
.card-head {
  display: flex; align-items: flex-start; gap: 8px; justify-content: space-between;
}
.card-title { font-weight: 600; font-size: 14.5px; line-height: 1.45; }
.card-extra { flex-shrink: 0; display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
.card-fields {
  margin-top: 8px; display: grid; grid-template-columns: auto 1fr;
  gap: 4px 10px; font-size: 13px;
}
.field { display: contents; }
.field-label { color: #909399; white-space: nowrap; }
.field-value { color: #303133; word-break: break-word; }
.card-body { margin-top: 8px; }
.card-actions {
  margin-top: 10px; padding-top: 10px; border-top: 1px dashed #eceef1;
  display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
}
</style>
