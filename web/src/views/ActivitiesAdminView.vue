<template>
  <div class="page page-wide">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
      <h2 style="margin:0">活动管理</h2>
      <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;新建活动</el-button>
    </div>

    <el-card shadow="never">
      <DataCards v-if="narrow" :rows="list" title-key="title" v-loading="loading"
                 :fields="[{ key: 'time_text', label: '时间' },
                           { key: 'location', label: '地点' }]"
                 empty="还没有活动">
        <template #extra="{ row }">
          <el-tag size="small" :type="statusType(row.status)">{{ row.status_label }}</el-tag>
        </template>
        <template #body="{ row }">
          <div class="dim" style="line-height:1.8">
            {{ row.type_label }} · 场次 {{ row.shifts_count }} ·
            已报名 {{ row.total_enrolled }}/{{ row.total_capacity }}
            <span v-if="row.waitlist_count">· 候补 {{ row.waitlist_count }}</span>
          </div>
        </template>
        <template #actions="{ row }">
          <el-button size="small" type="primary"
                     @click="$router.push('/manage/' + row.id)">管理</el-button>
          <el-button size="small" @click="openCopy(row)">复制</el-button>
          <el-popconfirm v-if="row.status === 'approved'" title="发布后学生即可报名，确认？"
                         @confirm="transition(row, 'publish')">
            <template #reference><el-button size="small" type="success" plain>发布</el-button></template>
          </el-popconfirm>
          <el-popconfirm v-if="row.status === 'published'" title="截止后不再接受新报名？"
                         @confirm="transition(row, 'close')">
            <template #reference><el-button size="small" type="warning" plain>截止</el-button></template>
          </el-popconfirm>
        </template>
      </DataCards>
      <el-table v-else :data="list" v-loading="loading">
        <el-table-column label="活动" min-width="240">
          <template #default="{row}">
            <b>{{ row.title }}</b>
            <div class="dim">{{ row.location || '待定' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="130">
          <template #default="{row}">
            <el-tag size="small" effect="plain" :color="typeColor(row.type)" style="border:none;color:#fff">
              {{ row.type_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{row}">
            <el-tag size="small" :type="statusType(row.status)">{{ row.status_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="场次/时间" min-width="170" prop="time_text" />
        <el-table-column label="报名" width="150">
          <template #default="{row}">
            {{ row.total_enrolled }}/{{ row.total_capacity }}
            <span v-if="row.waitlist_count" class="dim">+候补{{ row.waitlist_count }}</span>
            <el-progress :percentage="pct(row)" :stroke-width="4" :show-text="false" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="{row}">
            <el-button size="small" type="primary" link
                       @click="$router.push('/manage/' + row.id)">管理</el-button>
            <el-button size="small" link @click="openCopy(row)">复制</el-button>
            <el-popconfirm v-if="row.status === 'approved'" title="发布后学生即可报名，确认？"
                           @confirm="transition(row, 'publish')">
              <template #reference><el-button size="small" type="success" link>发布</el-button></template>
            </el-popconfirm>
            <el-popconfirm v-if="row.status === 'draft'"
                           title="删除该草稿？不可恢复" @confirm="delActivity(row)">
              <template #reference><el-button size="small" type="danger" link>删除</el-button></template>
            </el-popconfirm>
            <el-popconfirm v-if="row.status === 'published'" title="截止后不再接受新报名，确认？"
                           @confirm="transition(row, 'close')">
              <template #reference><el-button size="small" type="warning" link>截止</el-button></template>
            </el-popconfirm>
            <el-popconfirm v-if="['published','closed'].includes(row.status)"
                           title="结束活动后可进行时长录入与回传，确认？"
                           @confirm="transition(row, 'finish')">
              <template #reference><el-button size="small" type="danger" link>结束</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建活动 -->
    <el-dialog v-model="creating" title="新建活动（筹备期）" width="620">
      <ActivityForm v-model="form" v-model:range="createRange" :types="types" />
      <template #footer>
        <el-button @click="creating = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">创建草稿</el-button>
      </template>
    </el-dialog>

    <!-- 复制活动：只带结构，落草稿并直接进编辑页 -->
    <el-dialog v-model="copyDlg.visible" title="复制活动" width="500">
      <p class="dim" style="margin-top:0;line-height:1.8">
        复制「{{ copyDlg.source?.title }}」的<b>结构</b>：活动信息、场次、岗位、学习材料。<br />
        报名、签到、时长与留痕都不会带过来；副本落「筹备中」，共管名单只有您自己。
      </p>
      <el-form label-width="96px">
        <el-form-item label="延期天数" required>
          <el-input-number v-model="copyDlg.shiftDays" :min="-365" :max="365" />
          <span class="dim" style="margin-left:10px">
            报名窗口与所有场次时间整体后移这么多天
          </span>
          <div v-if="copyPreview" class="dim" style="margin-top:6px">
            场次时间：{{ copyPreview.from }} → <b>{{ copyPreview.to }}</b>
          </div>
        </el-form-item>
        <el-form-item label="新活动名称">
          <el-input v-model="copyDlg.title"
                    :placeholder="`${copyDlg.source?.title}（副本）`" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="copyDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="copyDlg.busy" @click="doCopy">
          复制并编辑
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { TYPE_COLORS } from '../store'
import { useNarrow } from '../composables/useNarrow'
import { activityBody, emptyActivity, validateActivity } from '../composables/activityForm'
import ActivityForm from '../components/ActivityForm.vue'
import DataCards from '../components/DataCards.vue'

const router = useRouter()
const narrow = useNarrow()
const list = ref([])
const loading = ref(false)
const creating = ref(false)
const saving = ref(false)
const types = ref({})
const copyDlg = ref({ visible: false, source: null, title: '', shiftDays: 7,
                      busy: false })
const form = ref({})
const createRange = ref([])

const typeColor = t => TYPE_COLORS[t]?.color
const statusType = s => ({ published: 'success', closed: 'info', draft: 'info', pending: 'warning', approved: 'primary', finished: 'info' }[s])
const pct = r => Math.min(100, Math.round(r.total_enrolled / Math.max(1, r.total_capacity) * 100))

function openCopy(row) {
  copyDlg.value = { visible: true, source: row, title: '', shiftDays: 7,
                    busy: false }
}

// 延期后场次落在哪天，边填边看，省得复制完才发现日子不对
const copyPreview = computed(() => {
  const src = copyDlg.value.source
  if (!src?.reg_end) return null
  const shift = (iso, days) => {
    const t = new Date(iso)
    t.setDate(t.getDate() + days)
    return `${t.getMonth() + 1}-${String(t.getDate()).padStart(2, '0')}`
  }
  return { from: shift(src.reg_end, 0), to: shift(src.reg_end, copyDlg.value.shiftDays) }
})

async function doCopy() {
  const d = copyDlg.value
  d.busy = true
  try {
    const r = await api.post(`/api/staff/activities/${d.source.id}/copy`,
      { title: d.title, shift_days: d.shiftDays })
    d.visible = false
    ElMessage.success(`已复制为「${r.title}」，请核对时间与岗位后发布`)
    router.push(`/manage/${r.id}`)      // 复制完直接进编辑页
  } finally { d.busy = false }
}

async function load() {
  loading.value = true
  try {
    list.value = await api.get('/api/staff/activities')
    types.value = (await api.get('/api/auth/constants')).activity_types
  } finally { loading.value = false }
}
onMounted(load)

function openCreate() {
  form.value = emptyActivity()
  createRange.value = []
  creating.value = true
}

async function save() {
  const err = validateActivity(form.value, createRange.value)
  if (err) return ElMessage.warning(err)
  saving.value = true
  try {
    const d = await api.post('/api/staff/activities',
                             activityBody(form.value, createRange.value))
    ElMessage.success('已创建草稿，请继续配置岗位')
    router.push('/manage/' + d.id)
  } catch { /* toast by api */ } finally {
    saving.value = false
    creating.value = false
  }
}

async function transition(row, action) {
  await api.post(`/api/staff/activities/${row.id}/${action}`)
  ElMessage.success('操作成功')
  load()
}

async function delActivity(row) {
  try {
    await api.del(`/api/staff/activities/${row.id}`)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    ElMessage.error(e.message)
  }
}
</script>

<style scoped>
.contacts-box { width: 100%; }
.contact-row { display: flex; gap: 8px; margin-bottom: 6px; align-items: center; }
.qr-preview { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.qr-preview img { max-width: 160px; max-height: 160px; border-radius: 8px; border: 1px solid #e4e7ed; }
</style>
