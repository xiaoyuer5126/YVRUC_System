<!-- 反馈截图的缩略图，点开看大图。
     截图只给提交人和管理员看，接口要鉴权：<img src> 带不了 Authorization 头，
     所以用带头的 fetch 取回来，转成本地 blob 地址再显示；组件卸载时释放。 -->
<template>
  <div v-if="images.length" class="shots">
    <el-image v-for="(src, i) in srcs" :key="images[i]?.id || i" :src="src"
              :preview-src-list="srcs" :initial-index="i" fit="cover"
              preview-teleported class="shot" />
  </div>
</template>

<script setup>
import { onUnmounted, ref, watch } from 'vue'
import { authHeaders } from '../api'
import { isOwnApi } from '../composables/authedImage'

const props = defineProps({ images: { type: Array, default: () => [] } })
const srcs = ref([])

function release() {
  srcs.value.forEach(u => u && URL.revokeObjectURL(u))
  srcs.value = []
}

async function load() {
  release()
  const got = await Promise.all(props.images.map(async (img) => {
    if (!isOwnApi(img.url)) return ''   // 只给本站接口带令牌
    try {
      const resp = await fetch(img.url, { headers: authHeaders() })
      return resp.ok ? URL.createObjectURL(await resp.blob()) : ''
    } catch {
      return ''
    }
  }))
  srcs.value = got
}

watch(() => props.images.map(i => i.id).join(','), load, { immediate: true })
onUnmounted(release)
</script>

<style scoped>
.shots { display: flex; gap: 8px; flex-wrap: wrap; }
.shot { width: 72px; height: 72px; border-radius: 6px; border: 1px solid #eceef1; }
</style>
