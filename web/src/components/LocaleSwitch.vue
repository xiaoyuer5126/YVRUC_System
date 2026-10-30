<template>
  <!-- 语言切换：一个文字按钮，点一下在中英之间来回。按钮上写的是「切过去之后」
       的语言，中文界面显示 EN，英文界面显示中文。
       登录页、签到落地页没有顶栏，用 float 把它摆到右上角。 -->
  <el-button text :class="{ float }" :title="t('切换语言')" @click="toggle">
    <span class="locale-btn">{{ next === 'en' ? 'EN' : '中文' }}</span>
  </el-button>
</template>

<script setup>
import { computed } from 'vue'
import { locale, setLocale, t } from '../i18n'

defineProps({ float: { type: Boolean, default: false } })

const next = computed(() => (locale.value === 'zh' ? 'en' : 'zh'))
const toggle = () => setLocale(next.value)
</script>

<style scoped>
.locale-btn { font-size: 13px; letter-spacing: .5px; }
.float { position: fixed; top: 8px; right: 12px; z-index: 200; }
</style>
