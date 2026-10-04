<template>
  <div class="page page-wide">
    <div class="page-head">
      <h2>活动管理</h2>
      <div style="display:flex;gap:8px">
        <!-- 立项书、结项书的空白模板，外联部长在「活动审批」页上传 -->
        <el-dropdown trigger="click" @command="t => download(`/api/materials/${t.file.id}`)">
          <el-button>材料模板<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-for="t in docTemplates" :key="t.slot" :command="t"
                                :disabled="!t.file">
                {{ t.label }}{{ t.file ? '' : '（对外联络部还没上传）' }}
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;新建活动</el-button>
      </div>
    </div>

    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="f.q" placeholder="搜索活动名称" clearable
                  style="width:220px" @input="onSearch" @clear="reload" />
        <!-- 筛选不带清除按钮，用「全部」那一项（空串）回到不筛：清除会把值置成
             undefined，拼进查询串就成了 status=undefined。空串默认被当作「没选」
             而显示占位文字，empty-values 去掉它，让「全部状态」照常显示出来 -->
        <el-select v-model="f.status" :empty-values="[null, undefined]"
                   style="width:150px" @change="reload">
          <el-option value="" label="全部状态" />
          <el-option v-for="(label, code) in STATUS_LABELS" :key="code"
                     :value="code" :label="label" />
        </el-select>
      </div>
      <DataCards v-if="narrow" :rows="list" title-key="title" v-loading="loading"
                 :fields="[{ key: 'time_text', label: '时间' },
                           { key: 'location', label: '地点' }]"
                 empty="还没有活动">
        <template #extra="{ row }">
          <el-tag size="small" :type="statusType(row.status)">{{ row.status_label }}</el-tag>
        </template>
        <template #body="{ row }">
          <div class="dim">
            <template v-if="row.record_only">{{ row.recruit_label }} · {{ row.time_text }}</template>
            <template v-else>
              {{ row.recruit_label }} · 场次 {{ row.shifts_count }} ·
              已报名 {{ row.total_enrolled }}/{{ row.total_capacity }}
              <span v-if="row.waitlist_count">· 候补 {{ row.waitlist_count }}</span>
            </template>
          </div>
        </template>
        <template #actions="{ row }">
          <el-button size="small" type="primary"
                     @click="$router.push('/manage/' + row.id)">管理</el-button>
          <el-button size="small" @click="openCopy(row)">复制</el-button>
          <el-popconfirm v-if="row.status === 'approved'" title="发布后学生即可看到活动，确认发布？"
                         @confirm="transition(row, 'publish')">
            <template #reference><el-button size="small" type="success" plain>发布</el-button></template>
          </el-popconfirm>
          <el-popconfirm v-if="row.status === 'published'" title="将截止报名，确认截止？"
                         @confirm="transition(row, 'close')">
            <template #reference><el-button size="small" type="warning" plain>截止</el-button></template>
          </el-popconfirm>
        </template>
      </DataCards>
      <el-table v-else :data="list" v-loading="loading">
        <el-table-column label="活动" min-width="240">
          <template #default="{row}">
            <el-tag v-if="row.urgent" size="small" type="danger" effect="dark"
                    style="margin-right:8px">急招</el-tag>
            <b>{{ row.title }}</b>
            <div class="dim">{{ row.location || '待定' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="130">
          <template #default="{row}">
            <el-tag size="small" effect="plain" :color="modeColor(row.recruit_mode)" style="border:none;color:#fff">
              {{ row.recruit_label }}
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
            <!-- 补记录活动不收报名，人数在「名单导入」里 -->
            <span v-if="row.record_only" class="dim">不公开 · 补记录</span>
            <template v-else>
              {{ row.total_enrolled }}/{{ row.total_capacity }}
              <span v-if="row.waitlist_count" class="dim">+候补{{ row.waitlist_count }}</span>
              <el-progress :percentage="pct(row)" :stroke-width="4" :show-text="false" />
            </template>
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
                           title="删除草稿不可恢复，确认删除？" @confirm="delActivity(row)">
              <template #reference><el-button size="small" type="danger" link>删除</el-button></template>
            </el-popconfirm>
            <el-popconfirm v-if="row.status === 'published'" title="将截止报名，确认截止？"
                           @confirm="transition(row, 'close')">
              <template #reference><el-button size="small" type="warning" link>截止</el-button></template>
            </el-popconfirm>
            <el-popconfirm v-if="['published','closed'].includes(row.status)"
                           title="结束活动后可进行时长录入，确认结束？"
                           @confirm="transition(row, 'finish')">
              <template #reference><el-button size="small" type="danger" link>结束</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination v-if="total > f.size" class="pager" layout="prev, pager, next"
                     background :current-page="f.page" :page-size="f.size"
                     :total="total" @current-change="goPage" />
    </el-card>

    <!-- 智能起草活动信息：草稿由管理者点了才填进下面的新建表单 -->
    <AiActivityAssist @fill="fillFromAi" />

    <!-- 新建活动 -->
    <el-dialog v-model="creating" title="新建活动（草稿）" width="620">
      <ActivityForm v-model="form" v-model:range="createRange" :modes="modes" />
      <template #footer>
        <el-button @click="creating = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">创建草稿</el-button>
      </template>
    </el-dialog>

    <!-- 复制活动：只带结构，落草稿并直接进编辑页 -->
    <el-dialog v-model="copyDlg.visible" title="复制活动" width="500">
      <p class="dim">
        复制「{{ copyDlg.source?.title }}」的<b>结构</b>：活动信息、场次、岗位、学习材料、报名问卷。
      </p>
      <el-form label-width="96px">
        <el-form-item label="延期天数" required>
          <el-input-number v-model="copyDlg.shiftDays" :min="-365" :max="365" />
          <span class="dim" style="margin-left:12px">
            报名时间与场次时间整体后移
          </span>
          <div v-if="copyPreview" class="dim" style="margin-top:8px">
            场次时间：{{ copyPreview.from }} → <b>{{ copyPreview.to }}</b>
          </div>
        </el-form-item>
        <el-form-item label="新活动名称">
          <el-input v-model="copyDlg.title"
                    :placeholder="`${copyDlg.source?.title}（副本）`" />
        </el-form-item>
        <!-- 新活动新群：二维码不随复制带过来，在这里就要传新图 -->
        <el-form-item label="志愿者联络群二维码" required>
          <div>
            <el-upload :show-file-list="false" :http-request="uploadCopyQr"
                       accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp">
              <el-button type="primary" plain :loading="copyDlg.uploading">
                上传图片
              </el-button>
            </el-upload>
            <img v-if="copyDlg.qr" :src="copyDlg.qr" class="copy-qr" />
          </div>
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
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, download } from '../api'
import { MODE_COLORS } from '../store'
import { useNarrow } from '../composables/useNarrow'
import { activityBody, emptyActivity, validateActivity } from '../composables/activityForm'
import { uploadImage } from '../composables/uploadImage'
import ActivityForm from '../components/ActivityForm.vue'
import AiActivityAssist from '../components/AiActivityAssist.vue'
import DataCards from '../components/DataCards.vue'

const router = useRouter()
const narrow = useNarrow()
const list = ref([])
const total = ref(0)
const f = reactive({ q: '', status: '', page: 1, size: 20 })
const STATUS_LABELS = {
  draft: '草稿', pending: '待审批', approved: '待发布', published: '报名中',
  closed: '已截止', finished: '已结束', vb_pending: '待外联确认', completed: '已完成',
}
const loading = ref(false)
const creating = ref(false)
const saving = ref(false)
const modes = ref({})
const copyDlg = ref({ visible: false, source: null, title: '', shiftDays: 7,
                      qr: '', uploading: false, busy: false })
const form = ref({})
const createRange = ref([])

const modeColor = m => MODE_COLORS[m]?.color
const statusType = s => ({ published: 'success', closed: 'info', draft: 'info', pending: 'warning', approved: 'primary', finished: 'info', vb_pending: 'warning', completed: 'success' }[s])
const pct = r => Math.min(100, Math.round(r.total_enrolled / Math.max(1, r.total_capacity) * 100))

function openCopy(row) {
  copyDlg.value = { visible: true, source: row, title: '', shiftDays: 7,
                    qr: '', uploading: false, busy: false }
}

async function uploadCopyQr({ file }) {
  copyDlg.value.uploading = true
  try {
    copyDlg.value.qr = await uploadImage(file)
  } catch (e) {
    ElMessage.error(e.message || '上传失败')
  } finally { copyDlg.value.uploading = false }
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
  if (!d.qr) return ElMessage.warning('请先上传新活动的志愿者联络群二维码')
  d.busy = true
  try {
    const r = await api.post(`/api/staff/activities/${d.source.id}/copy`,
      { title: d.title, shift_days: d.shiftDays, contact_group_qr: d.qr })
    d.visible = false
    ElMessage.success(`已复制为「${r.title}」，请核对时间与岗位后发布`)
    router.push(`/manage/${r.id}`)      // 复制完直接进编辑页
  } finally { d.busy = false }
}

async function load() {
  loading.value = true
  try {
    const query = new URLSearchParams({ q: f.q, status: f.status,
                                        page: f.page, size: f.size })
    const d = await api.get(`/api/staff/activities?${query}`)
    list.value = d.items
    total.value = d.total
    modes.value = (await api.get('/api/auth/constants')).recruit_modes
  } finally { loading.value = false }
}
onMounted(load)

const docTemplates = ref([])
onMounted(async () => {
  docTemplates.value = (await api.get('/api/staff/doc-templates', { silent: true })
    .catch(() => null))?.items || []
})

// 换筛选条件就回第一页：留在第 3 页多半是空的
function reload() {
  f.page = 1
  load()
}
function goPage(p) {
  f.page = p
  load()
}
let timer = null
function onSearch() {
  clearTimeout(timer)
  timer = setTimeout(reload, 300)
}

function openCreate() {
  form.value = emptyActivity()
  createRange.value = []
  creating.value = true
}

/** 智能起草的草稿盖在空表单上打开新建：没起草出来的字段保持默认（比如志愿者保障那句） */
function fillFromAi(draft) {
  const { reg_start: start, reg_end: end, ...rest } = draft
  form.value = { ...emptyActivity(), ...rest }
  createRange.value = start || end ? [start || '', end || ''] : []
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
    // 只有成功才关：服务端拦下的（重名等）弹窗得留着，改完还能直接再交一次
    creating.value = false
    router.push('/manage/' + d.id)
  } catch { /* toast by api */ } finally {
    saving.value = false
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
.contact-row { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
.qr-preview { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.qr-preview img { max-width: 160px; max-height: 160px; border-radius: 8px; border: 1px solid #e4e7ed; }
.copy-qr { width: 96px; height: 96px; margin-top: 8px; object-fit: contain;
           border: 1px solid #eceef1; border-radius: 8px; }
.pager { margin-top: 12px; justify-content: flex-end; }
</style>
