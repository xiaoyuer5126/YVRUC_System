<!-- 提醒志愿者先在「志愿北京」注册：时长最后要录进志愿北京，没注册的人录不进去。
     首次完善个人信息的弹窗和「我的志愿时长」页顶部共用这一句，改文案只改这里。 -->
<template>
  <el-alert type="info" :closable="false" show-icon class="vb-tip">
    <template #title>
      志愿时长最终要录入「志愿北京」，没注册的同学录不进去。还没注册的请尽快注册：电脑端打开
      <a :href="VB_SITE" target="_blank" rel="noopener">test1.bv2008.cn</a>，手机端在「京通」小程序里搜索「志愿北京」。
    </template>
  </el-alert>
</template>

<script setup>
// 2026 年 5 月起志愿北京换了新系统，电脑端走这个网址，手机端走京通小程序
const VB_SITE = 'https://test1.bv2008.cn/'
</script>

<style scoped>
.vb-tip { margin-bottom: 16px; }
</style>
