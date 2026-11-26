<!-- 活动上新提醒的订阅弹窗：「活动广场」标题旁的按钮打开。
     勾每周的空闲时段（周一到周日 × 上午/下午/晚上）、选关注的组织单位、
     填活动名关键词，新活动上广场时满足一样就提醒。三样都不填就是不订阅。
     时段怎么切、怎么匹配见 backend/watch.py。 -->
<template>
  <el-dialog v-model="visible" :title="t('活动上新提醒')" :width="narrow ? '94%' : '560px'"
             append-to-body :before-close="guard" @opened="guard.snap">
    <div v-loading="loading" class="watch">
      <p class="lead">{{ t('新活动上线时，如果有岗位的时段落在空闲时段里，或由你关注的组织单位举办，或活动名称包含关键词，就给你发一条上新提醒。') }}</p>

      <div class="sec">{{ t('每周空闲时段') }}</div>
      <div class="grid">
        <div />
        <button v-for="(d, i) in dayNames" :key="i" type="button" class="head"
                :class="{ full: dayFull(i) }" :aria-label="t('{0}，全选或清空这一天', [d])"
                @click="toggleDay(i)">{{ d }}</button>
        <template v-for="p in parts" :key="p.key">
          <button type="button" class="part" :aria-label="t('{0}，全选或清空这一行', [t(p.label)])"
                  @click="togglePart(p.key)">
            <span>{{ t(p.label) }}</span>
            <span class="dim">{{ p.start }}–{{ p.end }}</span>
          </button>
          <button v-for="(d, i) in dayNames" :key="i + p.key" type="button" class="cell"
                  :class="{ on: picked.has(`${i}-${p.key}`) }"
                  :aria-label="`${d} ${t(p.label)}`"
                  @click="toggle([`${i}-${p.key}`])" />
        </template>
      </div>
      <div class="foot-line dim">
        {{ t('已选 {0} 格', [slots.length]) }}<template v-if="slots.length"> ·
          <el-link type="primary" underline="never" @click="slots = []">{{ t('清空') }}</el-link>
        </template>
      </div>

      <div class="sec">{{ t('关注的组织单位') }}</div>
      <el-select v-model="organizers" multiple filterable clearable
                 :placeholder="t('从已有活动的组织单位里选，可多选')" style="width:100%">
        <el-option v-for="o in options" :key="o" :value="o" :label="o" />
      </el-select>

      <div class="sec">{{ t('活动名关键词') }}</div>
      <el-input-tag v-model="keywords" :max="maxKeywords" clearable
                    :placeholder="t('输入后按回车添加，最多 {0} 个', [maxKeywords])" />
      <div class="foot-line dim">{{ t('活动名包含任一关键词就提醒，不分大小写。每个最多 {0} 个字', [maxKeywordLen]) }}</div>

      <el-alert v-if="!slots.length && !organizers.length && !keywords.length" type="info"
                :closable="false" show-icon :title="t('都不选即为关闭提醒')" />
    </div>
    <template #footer>
      <el-button @click="visible = false">{{ t('取消') }}</el-button>
      <el-button type="primary" :loading="saving" :disabled="loading" @click="save">{{ t('保存') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { useCloseGuard } from '../composables/closeGuard'
import { useNarrow } from '../composables/useNarrow'
import { isEn, t } from '../i18n'

const props = defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(['update:modelValue', 'saved'])
const visible = computed({
  get: () => props.modelValue,
  set: v => emit('update:modelValue', v),
})
const narrow = useNarrow()

// 星期 0～6 对应周一～周日，和后端的格子键「星期-时段」一致
const DAYS = { zh: ['周一', '周二', '周三', '周四', '周五', '周六', '周日'],
               en: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] }
const dayNames = computed(() => (isEn.value ? DAYS.en : DAYS.zh))
const parts = ref([
  { key: 'am', label: '上午', start: 0, end: 12 },
  { key: 'pm', label: '下午', start: 12, end: 18 },
  { key: 'night', label: '晚上', start: 18, end: 24 },
])
const slots = ref([])
const organizers = ref([])
const keywords = ref([])
const maxKeywords = ref(5)
const maxKeywordLen = ref(20)
const options = ref([])
const loading = ref(false)
const saving = ref(false)
const guard = useCloseGuard(() => [[...slots.value].sort(), organizers.value, keywords.value])

const picked = computed(() => new Set(slots.value))

async function load() {
  loading.value = true
  try {
    const d = await api.get('/api/me/activity-watch')
    slots.value = d.slots
    organizers.value = d.organizers
    keywords.value = d.keywords
    maxKeywords.value = d.max_keywords
    maxKeywordLen.value = d.max_keyword_len
    options.value = d.organizer_options
    parts.value = d.parts
  } finally {
    loading.value = false
  }
  // 数据回来晚于 opened 时，快照要按回来的数据重拍，不然一打开就算「有改动」
  guard.snap()
}
watch(visible, v => { if (v) load() })

// 表头、行标签、单格共用：没全选就全选，已全选就全清
function toggle(keys) {
  const s = new Set(slots.value)
  if (keys.every(k => s.has(k))) keys.forEach(k => s.delete(k))
  else keys.forEach(k => s.add(k))
  slots.value = [...s]
}
const dayKeys = i => parts.value.map(p => `${i}-${p.key}`)
const dayFull = i => dayKeys(i).every(k => picked.value.has(k))
const toggleDay = i => toggle(dayKeys(i))
const togglePart = key => toggle([0, 1, 2, 3, 4, 5, 6].map(i => `${i}-${key}`))

// 关键词随改随整理：去首尾空格；空的、太长的、和前面重复（不分大小写）的去掉。
// 后端同样校验，这里先拦下免得保存时才报错。
watch(keywords, v => {
  const out = []
  let tooLong = false
  for (const raw of v) {
    const k = String(raw).trim()
    if (k.length > maxKeywordLen.value) tooLong = true
    else if (k && !out.some(x => x.toLowerCase() === k.toLowerCase())) out.push(k)
  }
  if (tooLong) ElMessage.warning(t('关键词每个最多 {0} 个字', [maxKeywordLen.value]))
  if (out.length !== v.length || out.some((k, i) => k !== v[i])) keywords.value = out
})

async function save() {
  saving.value = true
  try {
    const d = await api.put('/api/me/activity-watch',
      { slots: slots.value, organizers: organizers.value, keywords: keywords.value })
    ElMessage.success(d.slots.length || d.organizers.length || d.keywords.length
      ? t('已保存，有符合的新活动上线时会提醒你') : t('已关闭上新提醒'))
    emit('saved', d)
    visible.value = false
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.watch { min-height: 120px; }
.lead { margin: 0 0 16px; color: #606266; font-size: 13px; line-height: 1.6; }
.sec { font-size: 13px; font-weight: 600; color: #303133; margin-bottom: 8px; }
.grid { display: grid; grid-template-columns: 56px repeat(7, minmax(0, 1fr)); gap: 4px; }
.head { text-align: center; font-size: 12px; color: #303133; border: none; background: none;
        padding: 4px 0; font-family: inherit; cursor: pointer; border-radius: 4px; }
.head:hover { background: #ecf5ff; }
.head.full { color: var(--el-color-primary); font-weight: 600; }
.part { font-size: 12px; color: #606266; border: none; background: none; padding: 0;
        font-family: inherit; text-align: left; cursor: pointer; border-radius: 4px;
        display: flex; flex-direction: column; justify-content: center; line-height: 1.3; }
.part .dim { font-size: 11px; }
.part:hover { background: #ecf5ff; color: var(--el-color-primary); }
.cell { height: 36px; border: 1px solid #dcdfe6; border-radius: 4px; background: #fff;
        cursor: pointer; padding: 0; }
.cell.on { background: var(--el-color-primary); border-color: var(--el-color-primary); }
.foot-line { margin: 8px 0 16px; font-size: 12px; }
</style>
