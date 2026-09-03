<!-- 在活动名单里给某位志愿者登记一条违规。只记录，不产生任何后果。 -->
<template>
  <el-dialog v-model="visible" title="登记违规" width="480">
    <p style="margin-top:0">
      对象：<b>{{ row?.name }}</b><span class="dim">（{{ row?.student_id }}）</span>
    </p>
    <el-form label-width="88px">
      <el-form-item label="违规类型" required>
        <el-radio-group v-model="form.kind">
          <el-radio-button v-for="(label, code) in kinds" :key="code" :value="code">
            {{ label }}
          </el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="情况简述" required>
        <el-input v-model="form.detail" type="textarea" :rows="3" maxlength="500"
                  show-word-limit placeholder="发生了什么、什么时候。类型为「其他」时请尽量具体" />
      </el-form-item>
    </el-form>
    <div class="dim">只记入该志愿者的档案供青协查看，不影响他之后的报名。</div>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="save">登记</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const props = defineProps({ activityId: { type: String, required: true } })
const emit = defineEmits(['saved'])

const visible = ref(false)
const busy = ref(false)
const row = ref(null)
const kinds = ref({})
const form = ref({ kind: 'late', detail: '' })

onMounted(async () => {
  kinds.value = (await api.get('/api/auth/constants', { silent: true })).violation_kinds || {}
})

function open(user) {
  row.value = user
  form.value = { kind: 'late', detail: '' }
  visible.value = true
}
defineExpose({ open })

async function save() {
  if (!form.value.detail.trim()) return ElMessage.warning('请填写情况简述')
  busy.value = true
  try {
    await api.post('/api/staff/violations', {
      user_id: row.value.id, activity_id: props.activityId,
      kind: form.value.kind, detail: form.value.detail,
    })
    ElMessage.success('已记入档案')
    visible.value = false
    emit('saved')
  } finally { busy.value = false }
}
</script>
