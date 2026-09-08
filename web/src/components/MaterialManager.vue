<template>
  <div class="mgr">
    <div v-for="m in items" :key="m.id" class="row">
      <el-icon><Link v-if="m.kind === 'link'" /><Document v-else /></el-icon>
      <span class="name">{{ m.title }}</span>
      <span class="size">{{ m.kind === 'link' ? '链接' : sizeText(m.size) }}</span>
      <el-button link type="danger" size="small" @click="remove(m)">删除</el-button>
    </div>
    <!-- 岗位还没保存时先攒着，保存后由调用方一次性传上去 -->
    <div v-for="(m, i) in pending" :key="'p' + i" class="row">
      <el-icon><Link v-if="m.kind === 'link'" /><Document v-else /></el-icon>
      <span class="name">{{ m.title }}</span>
      <span class="size">{{ m.kind === 'link' ? '链接' : sizeText(m.file?.size) }}</span>
      <el-tag size="small" type="info" effect="plain">待保存</el-tag>
      <el-button link type="danger" size="small" @click="pending.splice(i, 1)">移除</el-button>
    </div>

    <div class="actions">
      <el-upload :show-file-list="false" :before-upload="upload" accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.png,.jpg,.jpeg,.txt">
        <el-button size="small" :loading="uploading">上传文档</el-button>
      </el-upload>
      <el-button size="small" @click="linkDlg = true">添加链接</el-button>
      <span class="dim">文档 ≤{{ limitMB }}MB，大文件请添加网盘或共享文档链接</span>
    </div>

    <el-dialog v-model="linkDlg" title="添加材料链接" width="440" append-to-body>
      <el-form label-width="72px">
        <el-form-item label="名称" required>
          <el-input v-model="link.title" placeholder="如：岗前培训视频" />
        </el-form-item>
        <el-form-item label="链接" required>
          <el-input v-model="link.url" placeholder="https://..." />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="linkDlg = false">取消</el-button>
        <el-button type="primary" @click="addLink">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Document, Link } from '@element-plus/icons-vue'
import { api } from '../api'
import { auth } from '../store'

const props = defineProps({
  ownerType: { type: String, required: true },   // position
  // 没有 ownerId = 对象还没建出来：先把文件攒在本地，保存后再传（见 flush）
  ownerId: { type: String, default: '' },
  items: { type: Array, default: () => [] },
})
const emit = defineEmits(['changed'])

// 待保存队列：[{kind:'file', file} | {kind:'link', title, url}]
const pending = ref([])

const limitMB = 50
const uploading = ref(false)
const linkDlg = ref(false)
const link = ref({ title: '', url: '' })

async function upload(file) {
  if (file.size > limitMB * 1024 * 1024) {
    ElMessage.error(`文件不能超过 ${limitMB}MB，请改用「添加链接」`)
    return false
  }
  if (!props.ownerId) {
    pending.value.push({ kind: 'file', title: file.name, file })
    return false
  }
  try { await send(file) } catch { /* 已弹过错误 */ }
  return false      // 交给我们自己的 fetch，禁用 el-upload 的上传
}

async function send(file, ownerId = props.ownerId) {
  const form = new FormData()
  form.append('owner_type', props.ownerType)
  form.append('owner_id', ownerId)
  form.append('file', file)
  uploading.value = true
  try {
    // FormData 不能走 api 的 JSON 封装，这里直接发
    const resp = await fetch('/api/staff/materials', {
      method: 'POST', body: form,
      headers: { Authorization: `Bearer ${auth.token}` },
    })
    const data = await resp.json()
    if (!resp.ok) throw new Error(data.detail || '上传失败')
    ElMessage.success('已上传')
    emit('changed')
  } catch (e) {
    ElMessage.error(e.message)
    throw e
  } finally { uploading.value = false }
}

async function addLink() {
  if (!link.value.title.trim() || !link.value.url.trim()) {
    return ElMessage.warning('请填写名称与链接')
  }
  if (!props.ownerId) {
    pending.value.push({ kind: 'link', ...link.value })
  } else {
    await api.post('/api/staff/materials/link', {
      owner_type: props.ownerType, owner_id: props.ownerId,
      title: link.value.title, url: link.value.url,
    })
    ElMessage.success('已添加')
    emit('changed')
  }
  linkDlg.value = false
  link.value = { title: '', url: '' }
}

/** 岗位保存后把攒着的材料传上去，返回失败的条数。
 *  失败不假装成功——调用方要如实告诉管理者哪几个没传上。 */
async function flush(ownerId) {
  const queue = pending.value
  pending.value = []
  let failed = 0
  for (const m of queue) {
    try {
      if (m.kind === 'file') {
        await send(m.file, ownerId)
      } else {
        await api.post('/api/staff/materials/link', {
          owner_type: props.ownerType, owner_id: ownerId,
          title: m.title, url: m.url,
        })
      }
    } catch { failed += 1 }
  }
  return failed
}

defineExpose({ flush, pendingCount: () => pending.value.length })

async function remove(m) {
  await api.del(`/api/staff/materials/${m.id}`)
  ElMessage.success('已删除')
  emit('changed')
}

function sizeText(bytes) {
  if (!bytes) return ''
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)}MB`
    : `${Math.max(1, Math.round(bytes / 1024))}KB`
}
</script>

<style scoped>
.mgr { width: 100%; }
.row { display: flex; align-items: center; gap: 8px; font-size: 13px; padding: 3px 0; }
.name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.size { color: #a8abb2; font-size: 13px; }
.actions { display: flex; align-items: center; gap: 8px; margin-top: 6px; flex-wrap: wrap; }
</style>
