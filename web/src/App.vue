<template>
  <!-- 组件自带的文字（日期选择器、分页、表格空态）跟着界面语言走 -->
  <el-config-provider :locale="elLocale">
    <router-view />
  </el-config-provider>
</template>

<script setup>
import { elLocale } from './i18n'
</script>
