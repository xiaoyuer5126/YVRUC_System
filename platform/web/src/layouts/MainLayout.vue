<template>
  <el-container class="app-root">
    <el-header class="topbar" height="56px">
      <div class="brand" @click="$router.push('/')">
        <div class="logo">志</div>
        <div>
          <div class="name">人大校青协志愿服务管理平台</div>
          <div class="sub">Volunteer Service Management · 中国人民大学青年志愿者协会</div>
        </div>
      </div>
      <div class="right">
        <template v-if="auth.user">
          <el-badge :value="unread" :hidden="!unread" class="msg-badge">
            <el-button text @click="$router.push('/messages')">
              <el-icon><Bell /></el-icon>&nbsp;消息
            </el-button>
          </el-badge>
          <el-dropdown @command="onCommand">
            <span class="who">
              <el-avatar :size="30" style="background:var(--el-color-primary)">
                {{ auth.user.name?.[0] }}
              </el-avatar>
              <span>{{ auth.user.name }}</span>
              <el-tag size="small" effect="light" style="color:#fff;background:rgba(255,255,255,.2);border:none">
                {{ ROLE_LABELS[auth.activeIdentity] || '学生' }}视角
              </el-tag>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <div class="switch-cap">切换身份（视角决定可见页面）</div>
                <el-dropdown-item v-for="r in auth.user.roles" :key="r" :command="'identity:' + r">
                  <el-icon v-if="auth.activeIdentity === r" style="color:var(--el-color-primary)"><Check /></el-icon>
                  {{ ROLE_LABELS[r] || r }}
                </el-dropdown-item>
                <el-dropdown-item divided command="my"
                                  v-if="auth.user.user_type === 'student'">我的报名</el-dropdown-item>
                <el-dropdown-item command="credit"
                                  v-if="auth.user.user_type === 'student'">我的信用分</el-dropdown-item>
                <el-dropdown-item command="hours"
                                  v-if="auth.user.user_type === 'student'">我的志愿时长</el-dropdown-item>
                <el-dropdown-item divided command="profile"
                                  v-if="auth.user.user_type === 'student'">完善个人信息</el-dropdown-item>
                <el-dropdown-item :divided="auth.user.user_type !== 'student'"
                                  command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </div>
    </el-header>
    <el-container class="app-body">
      <el-aside width="200px" class="side">
        <el-menu :default-active="$route.path" router class="side-menu">
          <template v-if="canSee.manage()">
            <div class="menu-cap">工作台（{{ ROLE_LABELS[auth.activeIdentity] }}）</div>
            <el-menu-item v-if="canSee.dashboard()" index="/dashboard">
              <el-icon><Odometer /></el-icon>数据总览
            </el-menu-item>
            <el-menu-item index="/manage"><el-icon><Calendar /></el-icon>活动管理</el-menu-item>
            <el-menu-item index="/audit"><el-icon><Document /></el-icon>审计日志</el-menu-item>
            <el-menu-item v-if="canSee.users()" index="/users">
              <el-icon><UserFilled /></el-icon>用户与信用
            </el-menu-item>
            <el-menu-item v-if="canSee.integrations()" index="/integrations">
              <el-icon><Link /></el-icon>集成设置
            </el-menu-item>
          </template>
          <template v-else>
            <div class="menu-cap">志愿服务</div>
            <el-menu-item index="/square"><el-icon><Compass /></el-icon>活动广场</el-menu-item>
            <el-menu-item index="/my"><el-icon><Tickets /></el-icon>我的报名</el-menu-item>
            <el-menu-item index="/credit"><el-icon><Star /></el-icon>信用分</el-menu-item>
            <el-menu-item index="/hours"><el-icon><Clock /></el-icon>志愿时长</el-menu-item>
          </template>
        </el-menu>
        <div class="side-foot dim">
          演示环境 · 对接数智人大/企业微信<br />接口已预留，见「集成设置」
        </div>
      </el-aside>
      <el-main class="app-main" style="padding:0">
        <router-view />
      </el-main>
    </el-container>

    <!-- 资料完善对话框：未完善的学生登录时强制弹出；也可从右上角菜单进入修改 -->
    <ProfileDialog ref="profileDialog" />
  </el-container>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { auth, canSee, clearAuth, setActiveIdentity, ROLE_LABELS } from '../store'
import ProfileDialog from '../components/ProfileDialog.vue'

const router = useRouter()
const unread = ref(0)
let timer = null

async function poll() {
  if (!auth.token) return
  try {
    const d = await api.get('/api/me/notifications', { silent: true })
    unread.value = d.unread
  } catch { /* 静默 */ }
}

const profileDialog = ref(null)
const openProfileDialog = (forced = false) => profileDialog.value.open(forced)

onMounted(() => {
  poll(); timer = setInterval(poll, 30000)
  // 学生资料未完善 → 强制弹出（不可关闭），补全前不可报名
  if (auth.token && auth.user?.user_type === 'student' && !auth.user.profile_complete) {
    openProfileDialog(true)
  }
})
onUnmounted(() => clearInterval(timer))

async function onCommand(cmd) {
  if (cmd === 'logout') {
    try { await api.post('/api/auth/logout', {}) } catch { /* 忽略 */ }
    clearAuth()
    router.push('/login')
    return
  }
  if (cmd === 'profile') {
    openProfileDialog(false)
    return
  }
  if (cmd.startsWith('identity:')) {
    setActiveIdentity(cmd.split(':')[1])
    router.push(canSee.manage() ? '/manage' : '/square')
    return
  }
  router.push('/' + cmd)
}
</script>

<style scoped>
/* 固定视口高度：侧边栏恒定满高，右侧内容独立滚动 */
.app-root { height: 100vh; }
.app-body { height: calc(100vh - 56px); }
.app-main { height: 100%; overflow-y: auto; }
.topbar {
  background: linear-gradient(90deg, #7e1b2a, var(--ruc-red));
  color: #fff; display: flex; align-items: center; justify-content: space-between;
  padding: 0 22px; position: sticky; top: 0; z-index: 100;
  flex-shrink: 0;
}
.brand { display: flex; gap: 12px; align-items: center; cursor: pointer; }
.logo {
  width: 38px; height: 38px; border-radius: 9px; background: rgba(255,255,255,.16);
  display: flex; align-items: center; justify-content: center;
  font-size: 20px; font-weight: 700; font-family: serif;
  border: 1px solid rgba(255,255,255,.35);
}
.name { font-size: 16px; font-weight: 600; letter-spacing: .5px; }
.sub { font-size: 11px; opacity: .75; }
.right { display: flex; align-items: center; gap: 14px; }
.who { display: flex; align-items: center; gap: 8px; color: #fff; cursor: pointer; outline: none; }
.side {
  background: #fff; border-right: 1px solid #eceef1;
  display: flex; flex-direction: column;
  height: 100%; overflow-y: auto; flex-shrink: 0;
}
.side-menu { border-right: none; flex: 1; }
.menu-cap { font-size: 12px; color: #a8abb2; padding: 14px 20px 4px; }
.switch-cap { font-size: 12px; color: #a8abb2; padding: 4px 16px 6px; border-bottom: 1px dashed #eceef1; margin-bottom: 4px; }
.side-foot { padding: 14px; line-height: 1.7; border-top: 1px dashed #eceef1; flex-shrink: 0; }
.msg-badge :deep(.el-button) { color: #fff; }
</style>
