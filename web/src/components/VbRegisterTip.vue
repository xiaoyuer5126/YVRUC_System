<!-- 提醒志愿者先在「志愿北京」注册：时长最后要录进志愿北京，没注册的人录不进去。
     首次完善个人信息的弹窗和「我的志愿时长」页顶部共用这一句，改文案只改这里。 -->
<template>
  <el-alert type="info" :closable="false" show-icon class="vb-tip">
    <template #title>
      志愿时长最终要录入「志愿北京」，没在志愿北京注册的同学录不进去。还没注册的请尽快在
      <a :href="VB_SITE" target="_blank" rel="noopener">志愿北京网站</a>
      或「京通」小程序里注册。
    </template>
  </el-alert>
</template>

<script setup>
// 2026 年 5 月起志愿北京换了新系统，注册走这个网址（或京通小程序）
const VB_SITE = 'https://test1.bv2008.cn/'
</script>

<style scoped>
.vb-tip { margin-bottom: 12px; }
</style>
