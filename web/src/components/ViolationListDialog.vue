<!-- 本活动登记过的全部违规：概览上点「违规记录」打开，登记错了在这里撤。 -->
<template>
  <el-dialog v-model="visible" title="本活动违规记录" :width="narrow ? '94%' : '720'">
    <div v-if="loading" class="dim">正在加载…</div>
    <div v-else-if="!items.length" class="dim">本活动还没有违规记录</div>
    <div v-else class="vio-list">
      <div v-for="v in items" :key="v.id" class="vio-item">
        <div class="vio-head">
          <span><b>{{ v.user_name || '—' }}</b> <span class="dim mono">{{ v.student_id }}</span></span>
          <el-tag size="small" type="warning" effect="plain">{{ v.kind_label }}</el-tag>
          <el-button size="small" link type="danger" :loading="busy === v.id"
                     style="margin-left:auto" @click="revoke(v)">撤销</el-button>
        </div>
        <div class="pre">{{ v.detail }}</div>
        <div class="dim">{{ v.operator_name || '—' }} 登记于 {{ fmtDT(v.created_at) }}</div>
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { api, fmtDT } from '../api'
import { useNarrow } from '../composables/useNarrow'
import { revokeViolation } from '../composables/violations'

const props = defineProps({ activityId: { type: String, required: true } })
const emit = defineEmits(['changed'])
const narrow = useNarrow()

const visible = ref(false)
const loading = ref(false)
const items = ref([])
const busy = ref('')

async function load() {
  loading.value = true
  try {
    items.value = (await api.get(`/api/staff/activities/${props.activityId}/violations`)).items
  } finally { loading.value = false }
}

function open() {
  items.value = []
  visible.value = true
  load()
}
defineExpose({ open })

async function revoke(v) {
  busy.value = v.id
  try {
    if (!await revokeViolation(v, v.user_name)) return
    await load()
    emit('changed')
  } finally { busy.value = '' }
}
</script>

<style scoped>
.vio-list { display: flex; flex-direction: column; gap: 8px; }
.vio-item { border: 1px solid #eceef1; border-radius: 4px; padding: 8px 12px; }
.vio-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pre { white-space: pre-wrap; }
</style>
