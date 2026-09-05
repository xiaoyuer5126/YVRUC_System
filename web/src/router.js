import { createRouter, createWebHashHistory } from 'vue-router'
import { auth, canSee, setActiveIdentity, hasIdentity, isManager } from './store'

const routes = [
  { path: '/login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
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
  if (!to.meta.public && !auth.token) {
    return { path: '/login', query: { next: to.fullPath } }
  }
  const manageView = ['admin', 'initiator', 'qingxie'].includes(auth.activeIdentity)

  // 首页按视角分流：工作台视角落地活动管理，学生视角落地活动广场
  if (to.path === '/') {
    return manageView ? '/manage' : '/square'
  }

  // 工作台视角不显示个人板块：个人页仅学生视角可进
  const personalPages = ['square', 'my', 'hours']
  if (manageView && personalPages.includes(to.path.slice(1))) {
    return '/manage'
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
    integrations: 'admin',
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
