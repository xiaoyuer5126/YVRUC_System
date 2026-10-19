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
    <el-upload v-if="canUpload" :show-file-list="false" :before-upload="upload"
               :accept="exts.map(e => '.' + e).join(',')">
      <el-button size="small" :loading="busy">{{ file ? '更换' : '上传' }}{{ label }}</el-button>
    </el-upload>
    <div v-if="canUpload" class="dim">{{ typeHint }}</div>
    <div v-else-if="block" class="dim">{{ block }}</div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Document } from '@element-plus/icons-vue'
import { download, fmtDT, uploadFile } from '../api'

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
</style>
