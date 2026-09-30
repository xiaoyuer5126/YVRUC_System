import { createRouter, createWebHashHistory } from 'vue-router'
import { auth, canSee, setActiveIdentity, hasIdentity, isManager } from './store'

const routes = [
  { path: '/login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
  // 统一身份认证回调。CAS 把 ?code=..&state=.. 追加在平台**根地址**上，
  // 所以这个页面从 window.location.search 取值，而不是路由 query。
  { path: '/sso-callback', component: () => import('./views/SsoCallbackView.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('./layouts/MainLayout.vue'),
    children: [
      { path: '', redirect: '/square' },
      // —— 学生端（个人） ——
      { path: 'square', component: () => import('./views/SquareView.vue') },
      { path: 'activity/:id', component: () => import('./views/ActivityDetailView.vue') },
      { path: 'my', component: () => import('./views/MyEnrollmentsView.vue') },
      { path: 'messages', component: () => import('./views/MessagesView.vue') },
      { path: 'hours', component: () => import('./views/HoursView.vue') },
      // —— 教师/青协端 ——
      { path: 'dashboard', component: () => import('./views/DashboardView.vue'), meta: { page: 'dashboard' } },
      { path: 'manage', component: () => import('./views/ActivitiesAdminView.vue'), meta: { page: 'manage' } },
      { path: 'manage/:id', component: () => import('./views/ActivityAdminView.vue'), meta: { page: 'manage' } },
      { path: 'approvals', component: () => import('./views/ApprovalsView.vue'), meta: { page: 'approvals' } },
      { path: 'audit', component: () => import('./views/AuditLogsView.vue'), meta: { page: 'audit' } },
      { path: 'users', component: () => import('./views/VolunteerArchiveView.vue'), meta: { page: 'users' } },
      { path: 'roles', component: () => import('./views/RolesView.vue'), meta: { page: 'roles' } },
      { path: 'integrations', component: () => import('./views/IntegrationsView.vue'), meta: { page: 'integrations' } },
      { path: 'feedback', component: () => import('./views/FeedbackAdminView.vue'), meta: { page: 'feedback' } },
    ],
  },
  { path: '/checkin/:token', component: () => import('./views/CheckinLanding.vue'), meta: { public: true } },
  { path: '/attire/:token', component: () => import('./views/AttireLanding.vue'), meta: { public: true } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

// 页面守卫：未登录去登录页；当前身份视角不够但持有对应身份时自动切换放行；
// 确实不持有则回对应首页（工作台视角→活动管理，学生视角→活动广场）。
router.beforeEach((to) => {
  // 统一身份认证回调要**最先**拦下来。查询串在真正的 URL 上、不在 hash 里，
  // vue-router 看不见它；不先放行的话，下面那条「没令牌就去登录页」会把人
  // 直接踢走，授权码白回来一趟。
  if (to.path !== '/sso-callback'
      && new URLSearchParams(window.location.search).has('code')) {
    return { path: '/sso-callback' }
  }
  if (!to.meta.public && !auth.token) {
    return { path: '/login', query: { next: to.fullPath } }
  }
  const manageView = ['admin', 'initiator', 'qingxie'].includes(auth.activeIdentity)

  // 首页按视角分流：工作台视角落地活动管理，学生视角落地活动广场
  if (to.path === '/') {
    return manageView ? '/manage' : '/square'
  }

  // 工作台视角不显示个人板块，个人页只在学生视角下进。直接打开个人页（多半是
  // 点了通知，比如企业微信里「时长已核准」的卡片）时，持有学生身份就切过去
  // 放行——不然人被弹到活动管理，还得自己切视角再把页面找回来。没有学生
  // 身份的（老师）才回活动管理
  const personalPages = ['square', 'my', 'hours']
  if (manageView && personalPages.includes(to.path.slice(1))) {
    if (!hasIdentity('student')) return '/manage'
    setActiveIdentity('student')
    return true
  }

  const page = to.meta.page
  if (!page || !auth.user) return

  // 用 store 里的 canSee，别在这儿抄第二份：抄一份就意味着以后新加页面
  // 只往一边加——角色管理就这么漏过，菜单里藏住了，直接敲地址却进得去。
  const need = canSee[page]
  if (!need || need()) return

  // 当前视角不够 → 若持有对应身份则自动切换视角放行
  const adminOr = hasIdentity('admin') ? 'admin' : 'qingxie'
  const wanted = {
    integrations: 'admin', feedback: 'admin',
    dashboard: adminOr, roles: adminOr, users: adminOr, approvals: adminOr,
    manage: hasIdentity('admin') ? 'admin' : hasIdentity('initiator') ? 'initiator' : 'qingxie',
    audit: hasIdentity('admin') ? 'admin' : hasIdentity('initiator') ? 'initiator' : 'qingxie',
  }[page]
  if (wanted && hasIdentity(wanted)) {
    setActiveIdentity(wanted)
    return true
  }
  return '/square'
})
