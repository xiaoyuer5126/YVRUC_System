<template>
  <div class="page page-wide">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
      <h2 style="margin:0">活动管理</h2>
      <el-button type="primary" @click="openCreate"><el-icon><Plus /></el-icon>&nbsp;新建活动</el-button>
    </div>

    <el-card shadow="never">
      <el-table :data="list" v-loading="loading">
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
            {{ row.total_confirmed }}/{{ row.total_capacity }}
            <span v-if="row.waitlist_count" class="dim">+候补{{ row.waitlist_count }}</span>
            <el-progress :percentage="pct(row)" :stroke-width="4" :show-text="false" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="{row}">
            <el-button size="small" type="primary" link
                       @click="$router.push('/manage/' + row.id)">管理</el-button>
            <el-button size="small" link @click="openCopy(row)">复制</el-button>
            <el-popconfirm v-if="row.status === 'draft'" title="发布后学生即可报名，确认？"
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
      <el-form label-width="110px">
        <el-form-item label="活动名称" required>
          <el-input v-model="form.title" maxlength="128" placeholder="如：校运动会开幕式志愿服务" />
        </el-form-item>
        <el-form-item label="活动类型" required>
          <el-radio-group v-model="form.type">
            <el-radio-button v-for="(t, key) in types" :key="key" :value="key">
              {{ key }}类·{{ t.name }}
            </el-radio-button>
          </el-radio-group>
          <div class="dim" style="margin-top:6px;line-height:1.8">
            <b>{{ form.type }}类·{{ types[form.type]?.full }}</b>：{{ types[form.type]?.desc }}<br />
            类型决定违规后的限制准入口径（《志愿者服务规范》），请按活动实际性质选择。
          </div>
        </el-form-item>
        <el-form-item label="活动地点" required>
          <el-input v-model="form.location" placeholder="如：世纪馆北门" />
        </el-form-item>
        <el-form-item label="公示联系方式" required>
          <div class="contacts-box">
            <div v-for="(c, i) in form.contacts" :key="i" class="contact-row">
              <el-input v-model="c.name" maxlength="32" placeholder="姓名" style="width:150px" />
              <el-input v-model="c.phone" maxlength="64" placeholder="电话/微信" style="flex:1" />
              <el-button type="danger" plain :disabled="form.contacts.length <= 1"
                         @click="form.contacts.splice(i, 1)">删除</el-button>
            </div>
            <el-button plain size="small"
                       @click="form.contacts.push({ name: '', phone: '' })">
              <el-icon><Plus /></el-icon>&nbsp;添加联系人
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="报名窗口" required>
          <el-date-picker v-model="createRange" type="datetimerange" style="width:100%"
                          :default-time="RANGE_DEFAULT_TIME"
                          start-placeholder="报名开始时间" end-placeholder="报名截止时间" />
          <div class="dim" style="margin-top:4px;line-height:1.7">
            报名开始与截止时间为必填；发布后可在活动管理页「编辑基本信息」调整。
          </div>
        </el-form-item>
        <el-form-item label="活动简介" required>
          <el-input v-model="form.intro" type="textarea" :rows="3"
                    placeholder="介绍活动内容、岗位与注意事项" />
        </el-form-item>
        <el-form-item label="答疑群二维码">
          <div class="contacts-box">
            <el-upload :show-file-list="false" :http-request="uploadQr" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp">
              <el-button type="primary" plain :loading="qrUploading">
                <el-icon><Upload /></el-icon>&nbsp;上传二维码图片
              </el-button>
            </el-upload>
            <div class="dim" style="margin-top:4px">选填；支持 png/jpg/jpeg/webp，不超过 2MB。活动信息交流答疑群，所有人均可扫码入群</div>
            <div v-if="form.qa_group_qr" class="qr-preview">
              <img :src="form.qa_group_qr" alt="答疑群二维码" />
              <el-button link type="danger" size="small" @click="form.qa_group_qr = ''">移除图片</el-button>
            </div>
          </div>
        </el-form-item>
      </el-form>
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
import { RANGE_DEFAULT_TIME, TYPE_COLORS, auth } from '../store'

const router = useRouter()
const list = ref([])
const loading = ref(false)
const creating = ref(false)
const saving = ref(false)
const qrUploading = ref(false)
const types = ref({})
const copyDlg = ref({ visible: false, source: null, title: '', shiftDays: 7,
                      busy: false })
const form = ref({})
const createRange = ref([])

function toISO(v) {
  if (!v) return null
  const d = v instanceof Date ? v : new Date(v)
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

const typeColor = t => TYPE_COLORS[t]?.color
const statusType = s => ({ published: 'success', closed: 'info', draft: 'warning', finished: 'info' }[s])
const pct = r => Math.min(100, Math.round(r.total_confirmed / Math.max(1, r.total_capacity) * 100))

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
  form.value = {
    title: '', type: 'A', location: '',
    contacts: [{ name: '', phone: '' }],
    intro: '', qa_group_qr: '',
  }
  createRange.value = []
  creating.value = true
}

// 自定义上传：POST /api/staff/upload-image（FormData），成功后取 url 预览
async function uploadQr({ file }) {
  const fd = new FormData()
  fd.append('file', file)
  qrUploading.value = true
  try {
    const headers = {}
    if (auth.token) headers.Authorization = `Bearer ${auth.token}`
    const resp = await fetch('/api/staff/upload-image', { method: 'POST', headers, body: fd })
    const d = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      throw new Error(typeof d.detail === 'string' ? d.detail : `上传失败(${resp.status})`)
    }
    form.value.qa_group_qr = d.url
    ElMessage.success('二维码已上传')
  } catch (e) {
    ElMessage.error(e.message || '上传失败')
  } finally {
    qrUploading.value = false
  }
}

function contactError(contacts) {
  if (!contacts?.length) return '请至少添加一位公示联系人'
  for (const c of contacts) {
    if (!c.name?.trim()) return '请填写每位联系人的姓名'
    if (!c.phone?.trim()) return '请填写每位联系人的电话/微信'
  }
  return ''
}

async function save() {
  const f = form.value
  if (!f.title.trim()) return ElMessage.warning('请填写活动名称')
  if (!f.type) return ElMessage.warning('请选择活动类型')
  if (!f.location.trim()) return ElMessage.warning('请填写活动地点')
  const ce = contactError(f.contacts)
  if (ce) return ElMessage.warning(ce)
  if (!(createRange.value?.length === 2 && createRange.value[0] && createRange.value[1])) {
    return ElMessage.warning('请选择报名开始与截止时间')
  }
  if (!f.intro.trim()) return ElMessage.warning('请填写活动简介')
  saving.value = true
  try {
    const body = {
      title: f.title, type: f.type, location: f.location,
      contacts: f.contacts.map(c => ({ name: c.name.trim(), phone: c.phone.trim() })),
      intro: f.intro, qa_group_qr: f.qa_group_qr,
      reg_start: toISO(createRange.value[0]),
      reg_end: toISO(createRange.value[1]),
    }
    const d = await api.post('/api/staff/activities', body)
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
