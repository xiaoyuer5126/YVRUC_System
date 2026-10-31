<!-- 一格文件：立项书、盖章立项书、盖章结项书，以及它们的空白模板。
     一格只留一份，传新的就替换。能不能换、收哪些类型都由后端说了算（block 是
     不能换的原因，exts 是能传的扩展名），这里只照着显示。
     template 是这一格对应的空白模板，有就在标题旁给个下载链接。 -->
<template>
  <div class="pf-slot">
    <div class="pf-head">
      <b>{{ label }}</b>
      <el-tag v-if="file" size="small" type="success" effect="plain">已上传</el-tag>
      <el-tag v-else size="small" :type="required ? 'danger' : 'info'" effect="plain">
        {{ required ? '未上传（必需）' : '未上传' }}
      </el-tag>
      <el-link v-if="template" type="primary" underline="never"
               @click="download(`/api/materials/${template.id}`)">下载模板</el-link>
    </div>
    <div v-if="file" class="pf-file">
      <el-icon><Document /></el-icon>
      <el-link type="primary" underline="never"
               @click="download(`/api/materials/${file.id}`)">{{ file.title }}</el-link>
      <span class="dim">{{ sizeText(file.size) }} · {{ fmtDT(file.uploaded_at) }}</span>
    </div>
    <div v-if="hint" class="dim">{{ hint }}</div>
    <div v-if="canUpload" class="pf-btns">
      <el-upload :show-file-list="false" :before-upload="upload"
                 :accept="exts.map(e => '.' + e).join(',')">
        <el-button size="small" :loading="busy">{{ file ? '更换' : '上传' }}{{ label }}</el-button>
      </el-upload>
      <!-- 收 PDF 的格子：拍了好几页照片的，挑好顺序在这里合成一份 PDF -->
      <template v-if="canMerge">
        <el-button size="small" :disabled="busy" @click="pickImages">多张图片合成 PDF</el-button>
        <input ref="picker" type="file" accept="image/png,image/jpeg" multiple hidden
               @change="onPicked" />
      </template>
    </div>
    <div v-if="canUpload" class="dim">{{ typeHint }}</div>
    <div v-else-if="block" class="dim">{{ block }}</div>

    <el-dialog v-model="merge.visible" :title="`多张图片合成${label}`"
               :width="narrow ? '94%' : '520'" append-to-body
               :before-close="guardMerge" @opened="guardMerge.snap">
      <div class="dim">按页码顺序排好，一张图一页，合成一个 PDF 后上传。</div>
      <div v-for="(it, i) in merge.items" :key="it.key" class="pf-page">
        <img :src="it.url" alt="" class="pf-thumb" />
        <span class="pf-name">第 {{ i + 1 }} 页 · {{ it.file.name }}</span>
        <span class="pf-ops">
          <el-button size="small" link :disabled="i === 0" @click="move(i, -1)">上移</el-button>
          <el-button size="small" link :disabled="i === merge.items.length - 1"
                     @click="move(i, 1)">下移</el-button>
          <el-button size="small" link type="danger" @click="drop(i)">移除</el-button>
        </span>
      </div>
      <el-button size="small" plain style="margin-top:8px" @click="pickImages">继续添加图片</el-button>
      <template #footer>
        <el-button @click="merge.visible = false">取消</el-button>
        <el-button type="primary" :loading="busy" :disabled="!merge.items.length"
                   @click="mergeUpload">合成并上传</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Document } from '@element-plus/icons-vue'
import { download, fmtDT, uploadFile } from '../api'
import { imagesToPdf } from '../composables/imagesToPdf'
import { useNarrow } from '../composables/useNarrow'
import { useCloseGuard } from '../composables/closeGuard'

const props = defineProps({
  activityId: { type: String, default: '' },
  kind: { type: String, required: true },        // proposal / stamped / closing
  label: { type: String, required: true },
  // 上传到哪；默认是活动的那几格，模板另有地址
  uploadUrl: { type: String, default: '' },
  template: { type: Object, default: null },
  file: { type: Object, default: null },
  canUpload: { type: Boolean, default: false },
  block: { type: String, default: '' },
  required: { type: Boolean, default: false },
  hint: { type: String, default: '' },
  exts: { type: Array, default: () => [] },
})
const emit = defineEmits(['changed'])
const busy = ref(false)
const LIMIT_MB = 50
// 上传按钮下面直接写清收什么、多大：类型由后端给，大小与这里拦的一致
const typeHint = computed(() => {
  const types = props.exts.length ? props.exts.join('/') + '，' : ''
  return `${types}单份不超过 ${LIMIT_MB}MB`
})

const narrow = useNarrow()
const canMerge = computed(() => props.exts.includes('pdf')
  && (props.exts.includes('jpg') || props.exts.includes('png')))
const picker = ref(null)
const merge = ref({ visible: false, items: [] })
const guardMerge = useCloseGuard(() => merge.value.items.map(it => it.key))
let seq = 0
const MAX_PAGES = 30

function pickImages() {
  picker.value.value = ''
  picker.value.click()
}
function onPicked(e) {
  const files = [...e.target.files].filter(f => /^image\/(png|jpeg)$/.test(f.type))
  if (!files.length) return ElMessage.warning('请选择 png 或 jpg 图片')
  if (!merge.value.visible) {
    merge.value.items = []
    // 选好的图一打开就是没上传的东西，关掉就没了
    guardMerge.markDirty()
  }
  // 相册里挑出来的顺序不一定是拍摄顺序，按文件名排一遍，拍照默认的文件名就是时间顺序
  files.sort((a, b) => a.name.localeCompare(b.name, 'zh', { numeric: true }))
  for (const f of files) {
    merge.value.items.push({ key: ++seq, file: f, url: URL.createObjectURL(f) })
  }
  if (merge.value.items.length > MAX_PAGES) {
    for (const it of merge.value.items.splice(MAX_PAGES)) URL.revokeObjectURL(it.url)
    ElMessage.warning(`最多合成 ${MAX_PAGES} 页`)
  }
  merge.value.visible = true
}
function move(i, d) {
  const a = merge.value.items
  ;[a[i], a[i + d]] = [a[i + d], a[i]]
}
function drop(i) {
  URL.revokeObjectURL(merge.value.items[i].url)
  merge.value.items.splice(i, 1)
}
// 关掉对话框就把缩略图的地址收回，不然每张图都在内存里留一份
watch(() => merge.value.visible, v => {
  if (v) return
  for (const it of merge.value.items) URL.revokeObjectURL(it.url)
  merge.value.items = []
})

async function mergeUpload() {
  busy.value = true
  let pdf
  try {
    pdf = await imagesToPdf(merge.value.items.map(it => it.file), props.label)
  } catch (e) {
    busy.value = false
    return ElMessage.error(e.message || '图片合成失败')
  }
  busy.value = false
  await upload(pdf)
  merge.value.visible = false
}

async function upload(f) {
  if (f.size > LIMIT_MB * 1024 * 1024) {
    ElMessage.error(`文件不能超过 ${LIMIT_MB}MB`)
    return false
  }
  const ext = f.name.split('.').pop().toLowerCase()
  if (props.exts.length && !props.exts.includes(ext)) {
    ElMessage.error(`${props.label}只能上传 ${props.exts.join('/')} 文件`)
    return false
  }
  busy.value = true
  try {
    await uploadFile(props.uploadUrl
      || `/api/staff/activities/${props.activityId}/project-files/${props.kind}`, f)
    ElMessage.success(`${props.label}已${props.file ? '更换' : '上传'}`)
    emit('changed')
  } catch { /* 错误已提示 */ } finally { busy.value = false }
  return false      // 自己发请求，不走 el-upload 的上传
}

function sizeText(bytes) {
  if (!bytes) return ''
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)}MB`
    : `${Math.max(1, Math.round(bytes / 1024))}KB`
}
</script>

<style scoped>
.pf-slot { display: flex; flex-direction: column; gap: 8px; padding: 8px 0; }
.pf-head { display: flex; align-items: center; gap: 8px; }
.pf-file { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pf-btns { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pf-page { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.pf-thumb { width: 48px; height: 48px; object-fit: cover; border-radius: 4px;
            border: 1px solid #eceef1; flex: none; }
.pf-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pf-ops { flex: none; }
</style>
