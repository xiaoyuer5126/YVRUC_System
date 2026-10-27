<!-- 校外时长补登：在校外自行报名参加的活动，本人登记、志愿北京对接部门审批，通过即
     计入志愿时长。这条账不属于任何平台活动（没有场次、报名、签到），所以单独
     一节，和上面按活动结的时长明细分开。 -->
<template>
  <el-card shadow="never" style="margin-top:16px">
    <template #header>
      <b>校外时长补登</b>
      <span class="dim" style="margin-left:8px">
        在校外自行报名参加的志愿活动，登记后由{{ vbDept }}审批，通过即计入时长
      </span>
    </template>

    <div class="toolbar">
      <el-button type="primary" @click="openForm">登记校外活动</el-button>
      <span class="dim">
        要填活动名称、活动日期、时长、服务描述，并上传证明材料和本人活动照片
      </span>
    </div>

    <DataCards v-if="narrow" :rows="items" title-key="title" empty="还没有登记过校外时长"
               :fields="[{ key: 'service_date', label: '活动日期' },
                         { key: 'hours', label: '申报时长', format: v => v + 'h' },
                         { key: 'status_label', label: '状态' }]">
      <template #extra="{ row }">
        <el-tag size="small" :type="STATUS_TYPES[row.status]">{{ row.status_label }}</el-tag>
      </template>
      <template #body="{ row }">
        <div v-if="row.status === 'approved'" class="dim">
          计入 {{ row.counted_hours }}h
        </div>
        <div v-if="row.reply" class="dim">
          {{ row.status === 'rejected' ? '驳回理由' : '说明' }}：{{ row.reply }}
        </div>
      </template>
      <template #actions="{ row }">
        <el-button v-if="row.can_withdraw" size="small" type="danger" plain
                   @click="withdraw(row)">撤回</el-button>
        <span v-else class="dim">—</span>
      </template>
    </DataCards>

    <el-table v-else :data="items" empty-text="还没有登记过校外时长">
      <el-table-column label="活动名称" min-width="220">
        <template #default="{ row }">
          <b>{{ row.title }}</b>
          <div v-if="row.status === 'approved'" class="dim">计入 {{ row.counted_hours }}h</div>
          <div v-if="row.reply" class="dim">
            {{ row.status === 'rejected' ? '驳回理由' : '说明' }}：{{ row.reply }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="活动日期" width="120" prop="service_date" />
      <el-table-column label="申报时长" width="100">
        <template #default="{ row }">{{ row.hours }}h</template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="STATUS_TYPES[row.status]">{{ row.status_label }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90">
        <template #default="{ row }">
          <el-button v-if="row.can_withdraw" size="small" type="danger" link
                     @click="withdraw(row)">撤回</el-button>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="form.visible" title="登记校外活动"
               :width="narrow ? '94%' : '620px'" @closed="reset"
               :close-on-click-modal="false"
               :before-close="guardForm" @opened="guardForm.snap">
      <p class="dim">
        只登记已经参加完的校外志愿活动。通过后计入志愿时长，并随「时长记录」
        报给志愿北京。
      </p>
      <el-form label-width="88px">
        <el-form-item label="活动名称" required>
          <el-input v-model="form.title" maxlength="128" show-word-limit
                    placeholder="例：XX 社区环保志愿服务" />
        </el-form-item>
        <el-form-item label="活动日期" required>
          <el-date-picker v-model="form.day" type="date" value-format="YYYY-MM-DD"
                          :disabled-date="isFuture" placeholder="参加活动的那一天"
                          style="width:100%" />
        </el-form-item>
        <el-form-item label="时长" required>
          <el-input-number v-model="form.hours" :min="0.5" :max="MAX_HOURS" :step="0.5" />
          <span class="dim" style="margin-left:12px">小时，按 0.5 小时填</span>
        </el-form-item>
        <el-form-item label="服务描述" required>
          <el-input v-model="form.desc" type="textarea" :rows="4" maxlength="1000"
                    show-word-limit
                    placeholder="做了什么、服务对象是谁、大致过程（20 字以上）" />
        </el-form-item>
        <el-form-item label="证明材料" required>
          <el-upload v-model:file-list="form.proofs" :auto-upload="false" multiple
                     :limit="MAX_FILES" accept="image/png,image/jpeg,image/webp,application/pdf"
                     :on-change="f => onPick(f, 'proofs')"
                     :on-exceed="() => onExceed('证明材料')">
            <el-button>选择文件</el-button>
          </el-upload>
          <div class="dim">
            主办方开的证明、盖章文件或服务记录，图片或 PDF，最多 {{ MAX_FILES }} 份、
            每份不超过 {{ MAX_MB }}MB
          </div>
        </el-form-item>
        <el-form-item label="活动照片" required>
          <el-upload v-model:file-list="form.photos" list-type="picture-card"
                     :auto-upload="false" multiple :limit="MAX_FILES"
                     accept="image/png,image/jpeg,image/webp"
                     :on-change="f => onPick(f, 'photos')"
                     :on-exceed="() => onExceed('活动照片')">
            <el-icon><Plus /></el-icon>
          </el-upload>
          <div class="dim">
            本人在活动现场的照片，最多 {{ MAX_FILES }} 张、每张不超过 {{ MAX_MB }}MB
          </div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.note" maxlength="256" show-word-limit
                    placeholder="选填，给审批的人看的补充说明" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="form.visible = false">取消</el-button>
        <el-button type="primary" :loading="form.busy" @click="submit">提交</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { api } from '../api'
import { useCloseGuard } from '../composables/closeGuard'
import { auth } from '../store'
import { useNarrow } from '../composables/useNarrow'
import DataCards from './DataCards.vue'

const emit = defineEmits(['total'])
const narrow = useNarrow()
// 志愿北京对接部门的实际名称（登录信息里带下来），文案里别写死部门名
const vbDept = computed(() => auth.user?.vb_dept_name || '校青协')
const STATUS_TYPES = { pending: 'warning', approved: 'success',
                       rejected: 'danger', revoked: 'info' }
const MAX_HOURS = 24
const MAX_FILES = 3
const MAX_MB = 5

const items = ref([])
const form = ref(empty())

function empty() {
  return { visible: false, title: '', day: '', hours: 2, desc: '',
           note: '', proofs: [], photos: [], busy: false }
}
const openForm = () => { form.value = { ...empty(), visible: true } }
const reset = () => { form.value = empty() }
const guardForm = useCloseGuard(() => ({ ...form.value, visible: 0, busy: 0, proofs: form.value.proofs.length, photos: form.value.photos.length }))

const load = async () => {
  const d = await api.get('/api/me/hours-claims')
  items.value = d.items
  emit('total', d.counted_total)
}
onMounted(load)

function onPick(file, which) {
  const raw = file.raw || {}
  const okType = which === 'proofs'
    ? ['image/png', 'image/jpeg', 'image/webp', 'application/pdf'].includes(raw.type)
    : ['image/png', 'image/jpeg', 'image/webp'].includes(raw.type)
  const okSize = (file.size || 0) <= MAX_MB * 1024 * 1024
  if (!okType || !okSize) {
    ElMessage.warning(okType
      ? `单个文件不能超过 ${MAX_MB}MB`
      : (which === 'proofs' ? '证明材料只支持图片和 PDF' : '活动照片只支持图片'))
    form.value[which] = form.value[which].filter(f => f.uid !== file.uid)
  }
}

function onExceed(what) {
  ElMessage.warning(`${what}最多 ${MAX_FILES} 份`)
}

// 只登记参加过的活动：今天以后的日子不给选（今天可以）
const isFuture = d => {
  const end = new Date()
  end.setHours(23, 59, 59, 999)
  return d > end
}

async function submit() {
  const f = form.value
  if (f.title.trim().length < 2) return ElMessage.warning('请填活动名称')
  if (!f.day) return ElMessage.warning('请选活动日期')
  if (f.desc.trim().length < 20) return ElMessage.warning('服务描述请写 20 字以上')
  if (!f.proofs.length) return ElMessage.warning('请上传证明材料')
  if (!f.photos.length) return ElMessage.warning('请上传本人活动照片')
  f.busy = true
  try {
    const body = new FormData()
    body.append('title', f.title.trim())
    body.append('service_date', f.day)
    body.append('hours', String(f.hours))
    body.append('service_desc', f.desc.trim())
    body.append('note', f.note.trim())
    f.proofs.forEach(x => body.append('proofs', x.raw))
    f.photos.forEach(x => body.append('photos', x.raw))
    await api.post('/api/me/hours-claims', body)
    ElMessage.success(`已提交，${vbDept.value}审批后会通知你`)
    f.visible = false
    load()
  } finally { f.busy = false }
}

async function withdraw(row) {
  try {
    await ElMessageBox.confirm(
      `撤回「${row.title}」这条登记？材料和照片会一起删掉，之后可以重新登记。`,
      '撤回补登', { confirmButtonText: '撤回', type: 'warning' })
  } catch { return }
  await api.post(`/api/me/hours-claims/${row.id}/withdraw`)
  ElMessage.success('已撤回')
  load()
}
</script>
