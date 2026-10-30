<template>
  <!-- 语言切换：一个文字按钮，点一下在中英之间来回。只翻志愿者端，
       管理端看着是英文按钮、点进去还是中文，所以管理视角下不摆出来 -->
  <el-button text :title="t('lang.switch')" @click="toggle">
    <span class="locale-btn">{{ next === 'en' ? 'EN' : '中文' }}</span>
  </el-button>
</template>

<script setup>
import { computed } from 'vue'
import { locale, setLocale, t } from '../i18n'

const next = computed(() => (locale.value === 'zh' ? 'en' : 'zh'))
const toggle = () => setLocale(next.value)
</script>

<style scoped>
.locale-btn { font-size: 13px; letter-spacing: .5px; }
</style>
