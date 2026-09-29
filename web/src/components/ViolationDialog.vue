<!-- 在活动名单里给某位志愿者登记一条违规。只记录，不产生任何后果。 -->
<template>
  <el-dialog v-model="visible" title="登记违规" width="480">
    <p>
      对象：<b>{{ row?.name }}</b><span class="dim">（{{ row?.student_id }}）</span>
    </p>
    <el-alert v-if="existing.length" type="warning" :closable="false" show-icon
              :title="`本活动已登记 ${existing.length} 条`" style="margin-bottom:12px">
      <div v-for="v in existing" :key="v.id">
        {{ v.kind_label }} · {{ fmtDT(v.created_at) }} · {{ v.operator_name || '—' }}：{{ v.detail }}
      </div>
    </el-alert>
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
                  show-word-limit placeholder="简述时间与经过，类型为「其他」时请尽量具体" />
      </el-form-item>
    </el-form>
    <div class="dim">仅记入该志愿者的档案供青协查看，不影响其报名。</div>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="save">登记</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { h, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, fmtDT } from '../api'

const props = defineProps({ activityId: { type: String, required: true } })
const emit = defineEmits(['saved'])

const visible = ref(false)
const busy = ref(false)
const row = ref(null)
const kinds = ref({})
const form = ref({ kind: 'late', detail: '' })
// 这个人在本活动已有的违规记录；拿不到（如部门成员）就当没有，提交时后端还会再拦一道
const existing = ref([])

onMounted(async () => {
  kinds.value = (await api.get('/api/auth/constants', { silent: true })).violation_kinds || {}
})

function open(user) {
  row.value = user
  form.value = { kind: 'late', detail: '' }
  existing.value = []
  visible.value = true
  api.get(`/api/staff/activities/${props.activityId}/volunteers/${user.id}/violations`,
    { silent: true })
    .then(d => { if (row.value?.id === user.id) existing.value = d.items })
    .catch(() => {})
}
defineExpose({ open })

async function save() {
  if (!form.value.detail.trim()) return ElMessage.warning('请填写情况简述')
  busy.value = true
  try {
    await submit(false)
  } finally { busy.value = false }
}

/** 同一活动已登记过时后端回 409 并附上已有记录：摆出来问一句，确认了再带标志重提。 */
async function submit(confirmDuplicate) {
  const body = {
    user_id: row.value.id, activity_id: props.activityId,
    kind: form.value.kind, detail: form.value.detail,
    confirm_duplicate: confirmDuplicate,
  }
  try {
    await api.post('/api/staff/violations', body, { skipErrorToast: true })
  } catch (e) {
    if (e.status !== 409 || e.data?.code !== 'VIOLATION_DUPLICATE') {
      // 其余错误照 api 层的规矩提示；401 那边已经提示过「登录已过期」
      if (e.data?.code === 'TRAINEE_READONLY') ElMessage.warning(e.message)
      else if (e.status !== 401) ElMessage.error(e.message)
      return
    }
    existing.value = e.data.items
    try {
      await ElMessageBox.confirm(
        h('div', [
          h('p', `${e.data.message}：`),
          h('ul', { style: 'padding-left:16px;margin:8px 0' }, e.data.items.map(v =>
            h('li', { key: v.id }, `${v.kind_label} · ${fmtDT(v.created_at)} · ` +
              `${v.operator_name || '—'}：${v.detail}`))),
          h('p', '仍要再登记一条吗？'),
        ]),
        '已有违规记录',
        { type: 'warning', confirmButtonText: '仍要登记', cancelButtonText: '取消' })
    } catch {
      return
    }
    return submit(true)
  }
  ElMessage.success('已记入档案')
  visible.value = false
  emit('saved')
}
</script>
