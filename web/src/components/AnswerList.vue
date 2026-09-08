<!-- 一个人的报名问卷答案，管理端看：文件题给下载链接，多选顿号连接。 -->
<template>
  <el-descriptions :column="1" size="small" border>
    <el-descriptions-item v-for="a in answers" :key="a.question_id" :label="a.title">
      <template v-if="a.kind === 'file'">
        <el-link v-if="a.value" type="primary" underline="never"
                 @click="download(`/api/materials/${a.value.attachment_id}`)">
          {{ a.value.title }}
        </el-link>
        <span v-else class="dim">未上传</span>
      </template>
      <span v-else-if="Array.isArray(a.value)">{{ a.value.join('、') || '—' }}</span>
      <span v-else style="white-space:pre-wrap">{{ a.value || '—' }}</span>
    </el-descriptions-item>
  </el-descriptions>
</template>

<script setup>
import { download } from '../api'

defineProps({ answers: { type: Array, default: () => [] } })
</script>
