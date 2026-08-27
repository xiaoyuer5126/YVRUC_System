<template>
  <el-container class="app-root">
    <el-header class="topbar" height="56px">
      <div class="left">
        <el-button v-if="narrow" text class="hamburger" aria-label="打开菜单"
                   @click="drawer = true">
          <el-icon :size="22"><Fold /></el-icon>
        </el-button>
        <div class="brand" @click="$router.push('/')">
          <div class="logo">志</div>
          <div class="brand-text">
            <div class="name">{{ narrow ? '校青协志愿平台' : '人大校青协志愿服务管理平台' }}</div>
            <div v-if="!narrow" class="sub">
              Volunteer Service Management · 中国人民大学青年志愿者协会
            </div>
          </div>
        </div>
      </div>

      <div class="right">
        <template v-if="auth.user">
          <el-badge :value="unread" :hidden="!unread" class="msg-badge">
            <el-button text @click="$router.push('/messages')">
              <el-icon><Bell /></el-icon>
              <span v-if="!narrow">&nbsp;消息</span>
            </el-button>
          </el-badge>
          <el-dropdown @command="onCommand">
            <span class="who">
              <el-avatar :size="30" style="background:var(--el-color-primary)">
                {{ auth.user.name?.[0] }}
              </el-avatar>
              <template v-if="!narrow">
                <span>{{ auth.user.name }}</span>
                <el-tag size="small" effect="light"
                        style="color:#fff;background:rgba(255,255,255,.2);border:none">
                  {{ ROLE_LABELS[auth.activeIdentity] || '学生' }}视角
                </el-tag>
              </template>
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
      <el-aside v-if="!narrow" width="200px" class="side">
        <SideMenu :active="$route.path" />
        <div class="side-foot dim">
          演示环境 · 对接数智人大/企业微信<br />接口已预留，见「集成设置」
        </div>
      </el-aside>
      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>

    <!-- 窄屏导航：同一份菜单收进抽屉，选完自动关 -->
    <el-drawer v-model="drawer" direction="ltr" size="264px" :with-header="false">
      <div class="drawer-head">
        <div class="logo">志</div>
        <div>
          <div class="name">校青协志愿平台</div>
          <div class="drawer-sub">
            {{ auth.user?.name }} · {{ ROLE_LABELS[auth.activeIdentity] || '学生' }}视角
          </div>
        </div>
      </div>
      <SideMenu :active="$route.path" @navigate="drawer = false" />
    </el-drawer>

    <!-- 资料完善对话框：未完善的学生登录时强制弹出；也可从右上角菜单进入修改 -->
    <ProfileDialog ref="profileDialog" />
  </el-container>
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowDown, Bell, Check, Fold } from '@element-plus/icons-vue'
import { api } from '../api'
import { auth, canSee, clearAuth, setActiveIdentity, ROLE_LABELS } from '../store'
import { useNarrow } from '../composables/useNarrow'
import ProfileDialog from '../components/ProfileDialog.vue'
import SideMenu from '../components/SideMenu.vue'

const router = useRouter()
const route = useRoute()
const narrow = useNarrow()
const drawer = ref(false)
const unread = ref(0)
let timer = null

// 抽屉里点的若是当前页，el-menu 不触发 select，这里兜住
watch(() => route.path, () => { drawer.value = false })

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
.app-main { height: 100%; overflow-y: auto; padding: 0; }
.topbar {
  background: linear-gradient(90deg, #7e1b2a, var(--ruc-red));
  color: #fff; display: flex; align-items: center; justify-content: space-between;
  padding: 0 22px; position: sticky; top: 0; z-index: 100;
  flex-shrink: 0; gap: 8px;
}
.left { display: flex; align-items: center; gap: 6px; min-width: 0; }
.hamburger { color: #fff; padding: 6px; }
.brand { display: flex; gap: 12px; align-items: center; cursor: pointer; min-width: 0; }
.brand-text { min-width: 0; }
.logo {
  width: 38px; height: 38px; border-radius: 9px; background: rgba(255,255,255,.16);
  display: flex; align-items: center; justify-content: center;
  font-size: 20px; font-weight: 700; font-family: serif;
  border: 1px solid rgba(255,255,255,.35); flex-shrink: 0;
}
.name { font-size: 16px; font-weight: 600; letter-spacing: .5px;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sub { font-size: 11px; opacity: .75; }
.right { display: flex; align-items: center; gap: 14px; flex-shrink: 0; }
.who { display: flex; align-items: center; gap: 8px; color: #fff; cursor: pointer; outline: none; }
.side {
  background: #fff; border-right: 1px solid #eceef1;
  display: flex; flex-direction: column;
  height: 100%; overflow-y: auto; flex-shrink: 0;
}
.side-foot { padding: 14px; line-height: 1.7; border-top: 1px dashed #eceef1;
             flex-shrink: 0; margin-top: auto; }
.msg-badge :deep(.el-button) { color: #fff; }
.switch-cap { font-size: 12px; color: #a8abb2; padding: 4px 16px 6px;
              border-bottom: 1px dashed #eceef1; margin-bottom: 4px; }

.drawer-head {
  display: flex; gap: 12px; align-items: center; padding: 16px;
  background: linear-gradient(90deg, #7e1b2a, var(--ruc-red)); color: #fff;
}
.drawer-sub { font-size: 12px; opacity: .8; margin-top: 2px; }

@media (max-width: 860px) {
  .topbar { padding: 0 10px; }
  .right { gap: 4px; }
  .name { font-size: 15px; }
  .logo { width: 32px; height: 32px; font-size: 17px; }
}
</style>
