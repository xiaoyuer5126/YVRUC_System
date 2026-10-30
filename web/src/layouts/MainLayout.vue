<template>
  <el-container class="app-root">
    <el-header class="topbar" height="56px">
      <div class="left">
        <el-button v-if="narrow" text class="hamburger" :aria-label="t('打开菜单')"
                   @click="drawer = true">
          <el-icon :size="22"><Fold /></el-icon>
        </el-button>
        <div class="brand" @click="$router.push('/')">
          <div class="logo">{{ isEn ? 'V' : '志' }}</div>
          <div class="brand-text">
            <div class="name">{{ narrow ? t('志愿服务平台') : t('志愿服务管理平台') }}</div>
            <div v-if="!narrow" class="sub">
              {{ isEn ? 'Renmin University of China' : 'Volunteer Service Management' }}
            </div>
          </div>
        </div>
      </div>

      <div class="right">
        <template v-if="auth.user">
          <!-- 反馈入口放在最前面：它是「找人说话」的入口，和消息一样属于常驻的
               两件事。姓名不写在按钮上——头像就是姓名首字，名字去菜单里看 -->
          <el-button text :title="t('意见反馈')" @click="openFeedback">
            <el-icon><ChatDotRound /></el-icon>
            <span v-if="!narrow">&nbsp;{{ t('反馈') }}</span>
          </el-button>
          <el-badge :value="unread" :hidden="!unread" class="msg-badge">
            <el-button text @click="$router.push('/messages')">
              <el-icon><Bell /></el-icon>
              <span v-if="!narrow">&nbsp;{{ t('消息') }}</span>
            </el-button>
          </el-badge>
          <!-- 语言切换只在志愿者视角摆出来：管理端不翻，那里切了也没变化 -->
          <LocaleSwitch v-if="volunteerView" class="locale-switch" />
          <el-dropdown @command="onCommand">
            <span class="who">
              <el-avatar :size="30" style="background:var(--el-color-primary)">
                {{ auth.user.name?.[0] }}
              </el-avatar>
              <template v-if="!narrow">
                <el-tag size="small" effect="light"
                        style="color:#fff;background:rgba(255,255,255,.2);border:none">
                  {{ identityLabel }}{{ isEn ? '' : '视角' }}
                </el-tag>
              </template>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <div class="switch-cap">{{ t('切换身份') }}</div>
                <el-dropdown-item v-for="r in auth.user.roles" :key="r" :command="'identity:' + r">
                  <el-icon v-if="auth.activeIdentity === r" style="color:var(--el-color-primary)"><Check /></el-icon>
                  {{ ROLE_LABELS[r] || r }}
                </el-dropdown-item>
                <el-dropdown-item divided command="my"
                                  v-if="auth.user.user_type === 'student'">{{ t('我的报名') }}</el-dropdown-item>
                <el-dropdown-item command="hours"
                                  v-if="auth.user.user_type === 'student'">{{ t('我的志愿时长') }}</el-dropdown-item>
                <el-dropdown-item divided command="profile"
                                  v-if="auth.user.user_type === 'student'">{{ t('完善个人信息') }}</el-dropdown-item>
                <el-dropdown-item command="logout">{{ t('退出登录') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </div>
    </el-header>

    <el-container class="app-body">
      <el-aside v-if="!narrow" width="175px" class="side">
        <SideMenu :active="$route.path" />
      </el-aside>
      <el-main class="app-main">
        <!-- 部门成员：按钮都在、都能点，只是最后一步不会保存。提示条常驻，
             否则他点了保存没生效，会以为系统坏了 -->
        <el-alert v-if="isTrainee()" type="warning" :closable="false" show-icon
                  class="trainee-bar" :title="t('你当前是部门成员（学习权限）：可以走完每一个流程，但提交时不会真正保存')" />
        <router-view />
      </el-main>
    </el-container>

    <!-- 窄屏导航：同一份菜单收进抽屉，选完自动关 -->
    <el-drawer v-model="drawer" direction="ltr" size="264px" :with-header="false"
               class="menu-drawer">
      <div class="drawer-head">
        <div class="logo">{{ isEn ? 'V' : '志' }}</div>
        <div>
          <div class="name">{{ t('志愿服务平台') }}</div>
          <div class="drawer-sub">
            {{ auth.user?.name }} · {{ identityLabel }}
          </div>
        </div>
      </div>
      <SideMenu :active="$route.path" @navigate="drawer = false" />
    </el-drawer>

    <!-- 资料完善对话框：未完善的学生登录时强制弹出；也可从右上角菜单进入修改 -->
    <ProfileDialog ref="profileDialog" />
    <!-- 意见反馈：任何身份都能提，入口在右上角菜单与窄屏抽屉 -->
    <FeedbackDialog ref="feedbackDialog" />
    <!-- 平台公告：登录后第一次进平台时弹一次 -->
    <AnnouncementDialog v-model="announcing" :title="announcement.title"
                        :body="announcement.body" />
  </el-container>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { isNavigationFailure, NavigationFailureType, useRoute, useRouter } from 'vue-router'
import { ArrowDown, Bell, ChatDotRound, Check, Fold } from '@element-plus/icons-vue'
import { api } from '../api'
import { auth, clearAuth, isTrainee, ROLE_LABELS, takeAnnouncementTurn, unreadState } from '../store'
import { switchIdentity } from '../router'
import { useNarrow } from '../composables/useNarrow'
import { isEn, t } from '../i18n'
import AnnouncementDialog from '../components/AnnouncementDialog.vue'
import FeedbackDialog from '../components/FeedbackDialog.vue'
import LocaleSwitch from '../components/LocaleSwitch.vue'
import ProfileDialog from '../components/ProfileDialog.vue'
import SideMenu from '../components/SideMenu.vue'

const router = useRouter()
const route = useRoute()
const narrow = useNarrow()
const drawer = ref(false)
const unread = computed(() => unreadState.count)
let timer = null

// 志愿者视角：切语言按钮只在这儿出现（管理端不翻）
const volunteerView = computed(() => !['admin', 'initiator', 'qingxie']
  .includes(auth.activeIdentity))

// 身份的英文名：管理端不翻，只有志愿者视角的顶栏这个标签用
const EN_ROLE = {
  admin: 'Administrator', qingxie: 'Youth Volunteers', initiator: 'Organizer',
  student: 'Volunteer',
}

// 当前视角的称呼：没有对应身份时（比如还没被授予任何身份的老师，默认视角是
// student）按身份类型说，别把老师叫成「志愿者」
const identityLabel = computed(() => {
  const roles = auth.user?.roles || []
  if (roles.includes(auth.activeIdentity)) {
    if (isEn.value) return EN_ROLE[auth.activeIdentity] || ROLE_LABELS[auth.activeIdentity]
    return ROLE_LABELS[auth.activeIdentity]
  }
  return auth.user?.user_type === 'teacher' ? t('教职工') : t('志愿者')
})

// 抽屉里点的若是当前页，el-menu 不触发 select，这里兜住
watch(() => route.path, () => { drawer.value = false })

async function poll() {
  if (!auth.token) return
  try {
    // 只问数字：完整那个接口会把一页通知的正文一并拉回来，
    // 而这儿只用得上一个 unread。30 秒一次 × 每个开着的页面，省下来不少
    const d = await api.get('/api/me/notifications/unread', { silent: true })
    unreadState.count = d.unread
  } catch { /* 静默 */ }
}

const profileDialog = ref(null)
const openProfileDialog = (forced = false) => profileDialog.value.open(forced)
const feedbackDialog = ref(null)
function openFeedback() {
  drawer.value = false
  feedbackDialog.value.open()
}

// 「意见反馈已处理」通知的落点是 /messages?feedback=mine：把「我的反馈」弹窗
// 打开，再把查询串从地址栏去掉——不删的话刷新或后退会又弹一次。企业微信卡片
// 直接打开这个地址（整页加载），所以挂载时也要认一次，不能只听路由变化。
async function openFeedbackFromQuery() {
  if (route.query.feedback !== 'mine') return
  drawer.value = false
  feedbackDialog.value?.open('mine')
  const query = { ...route.query }
  delete query.feedback
  // 只改查询串，路径和路由参数原样保留
  await router.replace({ query })
}
watch(() => route.query.feedback, openFeedbackFromQuery)

// 本地存的用户信息是登录那一刻的：部门、层级、值班资格事后可能被调过，
// 打开页面时刷一次，菜单（比如「活动审批」）才跟得上
async function refreshUser() {
  try {
    const d = await api.get('/api/auth/me', { silent: true })
    auth.user = d.user
    localStorage.setItem('yvruc_user', JSON.stringify(d.user))
  } catch { /* 静默：401 由 api 层处理 */ }
}

const announcing = ref(false)
const announcement = ref({ title: '', body: '' })
async function showAnnouncement() {
  if (!auth.token || !takeAnnouncementTurn()) return
  try {
    const d = await api.get('/api/announcement', { silent: true })
    if (d.announcement) {
      announcement.value = d.announcement
      announcing.value = true
    }
  } catch { /* 静默：公告取不到不影响用平台 */ }
}

onMounted(() => {
  poll(); timer = setInterval(poll, 30000)
  if (auth.token) refreshUser()
  showAnnouncement()
  openFeedbackFromQuery()
  // 学生资料未完善 → 强制弹出（不可关闭），补全前不可报名
  if (auth.token && auth.user?.user_type === 'student' && !auth.user.profile_complete) {
    openProfileDialog(true)
  }
})
onUnmounted(() => clearInterval(timer))

async function onCommand(cmd) {
  if (cmd === 'logout') {
    // 先离开当前页、再注销：离开时页面可能要拦一下（活动管理页「改动还没发布」，
    // 里面点「保存并发布」要带着令牌请求后端）。先注销的话那一下必定 401，
    // 跳转被中止，人卡在一个已经退出了的页面上。
    // 带 signout 让守卫放这一跳直接去登录页：不带的话它会被「已登录访问登录页
    // → 回首页」重定向，而重定向要重走一遍导航，离开拦截跑第二遍、弹窗弹两次。
    // 只有被拦下（aborted）才算人选了「留下」，这时什么都不做
    const failure = await router.push({ path: '/login', query: { signout: '1' } })
    if (isNavigationFailure(failure, NavigationFailureType.aborted)) return
    try { await api.post('/api/auth/logout', {}) } catch { /* 忽略 */ }
    clearAuth()
    // 令牌清掉之后那个标记就没用了，从地址栏去掉
    router.replace({ path: '/login' })
    return
  }
  if (cmd === 'profile') {
    openProfileDialog(false)
    return
  }
  if (cmd.startsWith('identity:')) {
    switchIdentity(cmd.split(':')[1])
    return
  }
  router.push('/' + cmd)
}
</script>

<style scoped>
.trainee-bar { margin-bottom: 12px; }
/* 固定视口高度：侧边栏恒定满高，右侧内容独立滚动 */
.app-root { height: 100vh; }
.app-body { height: calc(100vh - 56px); }
.app-main { height: 100%; overflow-y: auto; padding: 0; scrollbar-gutter: stable; }
.topbar {
  background: var(--ruc-red);
  color: #fff; display: flex; align-items: center; justify-content: space-between;
  padding: 0 22px; position: sticky; top: 0; z-index: 100;
  flex-shrink: 0; gap: 8px;
}
.left { display: flex; align-items: center; gap: 8px; min-width: 0; }
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
.right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
/* 深红顶栏上的图标按钮：el-button 自己带一套文字色（默认深色），
   在这儿压成白的，反馈和消息两个图标才是同一种颜色 */
.right .el-button { color: #fff; }
.who { display: flex; align-items: center; gap: 8px; color: #fff; cursor: pointer; outline: none; }
.side {
  background: #fff; border-right: 1px solid #eceef1;
  display: flex; flex-direction: column;
  height: 100%; overflow-y: auto; flex-shrink: 0;
}
.side-foot { padding: 14px; line-height: 1.5; border-top: 1px dashed #eceef1;
             flex-shrink: 0; margin-top: auto; }
.msg-badge :deep(.el-button) { color: #fff; }
.switch-cap { font-size: 13px; color: #a8abb2; padding: 4px 16px 6px;
              border-bottom: 1px dashed #eceef1; margin-bottom: 4px; }

.drawer-head {
  display: flex; gap: 12px; align-items: center; padding: 16px;
  background: var(--ruc-red); color: #fff;
}
.drawer-sub { font-size: 12px; opacity: .8; margin-top: 4px; }
.drawer-foot { padding: 8px 12px; border-top: 1px dashed #eceef1; }

@media (max-width: 860px) {
  .topbar { padding: 0 10px; }
  .right { gap: 4px; }
  .name { font-size: 15px; }
  .logo { width: 32px; height: 32px; font-size: 17px; }
}
</style>
