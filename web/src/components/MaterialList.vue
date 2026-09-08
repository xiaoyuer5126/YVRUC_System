<template>
  <div v-if="items.length" class="materials">
    <div class="materials-title">{{ label }}</div>
    <a v-for="m in items" :key="m.id" class="material" :href="href(m)"
       :target="m.kind === 'link' ? '_blank' : '_self'"
       :download="m.kind === 'file' ? m.title : undefined"
       rel="noopener">
      <el-icon><Link v-if="m.kind === 'link'" /><Document v-else /></el-icon>
      <span class="name">{{ m.title }}</span>
      <span v-if="m.kind === 'file'" class="size">{{ sizeText(m.size) }}</span>
      <span v-else class="size">外部链接</span>
    </a>
  </div>
</template>

<script setup>
import { Document, Link } from '@element-plus/icons-vue'
import { auth } from '../store'

defineProps({
  items: { type: Array, default: () => [] },
  label: { type: String, default: '学习材料' },
})

// 文件下载走接口鉴权：<a> 发不出 Authorization 头，只能把令牌挂在地址上
function href(m) {
  return m.kind === 'link'
    ? m.url
    : `${m.url}?token=${encodeURIComponent(auth.token)}`
}

function sizeText(bytes) {
  if (!bytes) return ''
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)}MB`
    : `${Math.max(1, Math.round(bytes / 1024))}KB`
}
</script>

<style scoped>
.materials { margin-top: 10px; }
.materials-title { font-size: 12.5px; color: #909399; margin-bottom: 6px; }
.material {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 10px; margin: 0 8px 8px 0;
  border: 1px solid var(--el-border-color); border-radius: 6px;
  font-size: 13px; color: var(--el-text-color-primary); text-decoration: none;
}
.material:hover { border-color: var(--el-color-primary); color: var(--el-color-primary); }
.name { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.size { color: #a8abb2; font-size: 13px; }
</style>
