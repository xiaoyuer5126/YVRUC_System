<!-- 导航菜单。宽屏挂在固定侧栏里，窄屏收进抽屉——只维护这一份。 -->
<template>
  <el-menu :default-active="active" router class="side-menu" @select="$emit('navigate')">
    <template v-if="canSee.manage()">
      <div class="menu-cap">工作台（{{ ROLE_LABELS[auth.activeIdentity] }}）</div>
      <el-menu-item v-if="canSee.dashboard()" index="/dashboard">
        <el-icon><Odometer /></el-icon>数据总览
      </el-menu-item>
      <el-menu-item index="/manage"><el-icon><Calendar /></el-icon>活动管理</el-menu-item>
      <el-menu-item v-if="canSee.approvals()" index="/approvals">
        <el-icon><Stamp /></el-icon>活动审批
      </el-menu-item>
      <el-menu-item index="/audit"><el-icon><Document /></el-icon>审计日志</el-menu-item>
      <el-menu-item v-if="canSee.users()" index="/users">
        <el-icon><UserFilled /></el-icon>志愿者档案
      </el-menu-item>
      <el-menu-item v-if="canSee.roles()" index="/roles">
        <el-icon><Key /></el-icon>角色管理
      </el-menu-item>
      <el-menu-item v-if="canSee.integrations()" index="/integrations">
        <el-icon><Link /></el-icon>集成设置
      </el-menu-item>
      <el-menu-item v-if="canSee.feedback()" index="/feedback">
        <el-icon><ChatDotRound /></el-icon>意见反馈
      </el-menu-item>
    </template>
    <template v-else>
      <div class="menu-cap">志愿服务</div>
      <el-menu-item index="/square"><el-icon><Compass /></el-icon>活动广场</el-menu-item>
      <el-menu-item index="/my"><el-icon><Tickets /></el-icon>我的报名</el-menu-item>
      <el-menu-item index="/hours"><el-icon><Clock /></el-icon>志愿时长</el-menu-item>
    </template>
    <el-menu-item index="/messages"><el-icon><Bell /></el-icon>消息通知</el-menu-item>
  </el-menu>
</template>

<script setup>
import {
  Bell, Calendar, ChatDotRound, Clock, Compass, Document, Key, Link, Odometer, Stamp,
  Tickets, UserFilled,
} from '@element-plus/icons-vue'
import { auth, canSee, ROLE_LABELS } from '../store'

defineProps({ active: { type: String, default: '' } })
defineEmits(['navigate'])
</script>

<style scoped>
.side-menu { border-right: none; }
.menu-cap { font-size: 13px; color: #a8abb2; padding: 14px 20px 4px; }
</style>
