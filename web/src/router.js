import { createRouter, createWebHashHistory } from 'vue-router'
import { auth, setActiveIdentity, hasIdentity, isManager } from './store'

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
      { path: 'credit', component: () => import('./views/CreditView.vue') },
      { path: 'hours', component: () => import('./views/HoursView.vue') },
      // —— 教师/青协端 ——
      { path: 'dashboard', component: () => import('./views/DashboardView.vue'), meta: { page: 'dashboard' } },
      { path: 'manage', component: () => import('./views/ActivitiesAdminView.vue'), meta: { page: 'manage' } },
      { path: 'manage/:id', component: () => import('./views/ActivityAdminView.vue'), meta: { page: 'manage' } },
      { path: 'audit', component: () => import('./views/AuditLogsView.vue'), meta: { page: 'audit' } },
      { path: 'users', component: () => import('./views/UsersCreditView.vue'), meta: { page: 'users' } },
      { path: 'integrations', component: () => import('./views/IntegrationsView.vue'), meta: { page: 'integrations' } },
    ],
  },
  { path: '/checkin/:token', component: () => import('./views/CheckinLanding.vue'), meta: { public: true } },
  { path: '/vest/:token', component: () => import('./views/VestLanding.vue'), meta: { public: true } },
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
  const manageView = ['admin', 'teacher', 'qingxie'].includes(auth.activeIdentity)

  // 首页按视角分流：工作台视角落地活动管理，学生视角落地活动广场
  if (to.path === '/') {
    return manageView ? '/manage' : '/square'
  }

  // 工作台视角不显示个人板块：个人页仅学生视角可进
  const personalPages = ['square', 'my', 'credit', 'hours']
  if (manageView && personalPages.includes(to.path.slice(1))) {
    return '/manage'
  }

  const page = to.meta.page
  if (!page || !auth.user) return

  const need = {
    dashboard: () => auth.activeIdentity === 'admin',
    manage: () => ['admin', 'teacher', 'qingxie'].includes(auth.activeIdentity),
    audit: () => ['admin', 'teacher', 'qingxie'].includes(auth.activeIdentity),
    users: () => ['admin', 'qingxie'].includes(auth.activeIdentity),
    integrations: () => auth.activeIdentity === 'admin',
  }[page]
  if (!need || need()) return

  // 当前视角不够 → 若持有对应身份则自动切换视角放行
  const wanted = {
    dashboard: 'admin', integrations: 'admin',
    users: hasIdentity('admin') ? 'admin' : 'qingxie',
    manage: hasIdentity('admin') ? 'admin' : hasIdentity('teacher') ? 'teacher' : 'qingxie',
    audit: hasIdentity('admin') ? 'admin' : hasIdentity('teacher') ? 'teacher' : 'qingxie',
  }[page]
  if (wanted && hasIdentity(wanted)) {
    setActiveIdentity(wanted)
    return true
  }
  return '/square'
})
