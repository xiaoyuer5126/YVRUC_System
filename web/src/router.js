import { createRouter, createWebHistory, isNavigationFailure, NavigationFailureType } from 'vue-router'
import { auth, canSee, setActiveIdentity, hasIdentity, isManager } from './store'

const routes = [
  { path: '/login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
  // 统一身份认证回调。CAS 把 ?code=..&state=.. 追加在平台**根地址**上，
  // 由下面的守卫连同查询串一起转到这一页。
  { path: '/sso-callback', component: () => import('./views/SsoCallbackView.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('./layouts/MainLayout.vue'),
    // 根地址不设子路由重定向：重定向在守卫之前就生效，守卫看到的已经是
    // /square，下面「首页按视角分流」就轮不到，工作台视角的人一进首页
    // 就被「个人页切学生视角」那条切走。根地址由守卫按视角分流
    children: [
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
      { path: 'volunteers', component: () => import('./views/VolunteerArchiveView.vue'), meta: { page: 'volunteers' } },
      { path: 'roles', component: () => import('./views/RolesView.vue'), meta: { page: 'roles' } },
      { path: 'integrations', component: () => import('./views/IntegrationsView.vue'), meta: { page: 'integrations' } },
      { path: 'announcement', component: () => import('./views/AnnouncementView.vue'), meta: { page: 'announcement' } },
      { path: 'oplogs', component: () => import('./views/OplogsView.vue'), meta: { page: 'oplogs' } },
      { path: 'feedback', component: () => import('./views/FeedbackAdminView.vue'), meta: { page: 'feedback' } },
    ],
  },
  { path: '/checkin/:token', component: () => import('./views/CheckinLanding.vue'), meta: { public: true } },
  { path: '/attire/:token', component: () => import('./views/AttireLanding.vue'), meta: { public: true } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 页面守卫：未登录去登录页；当前身份视角不够但持有对应身份时自动切换放行；
// 确实不持有则回对应首页（工作台视角→活动管理，学生视角→活动广场）。
// 切换身份视角：先去新视角的首页，离开当前页的拦截都放行了才换视角。
// 要是先换，拦截框（活动管理页「改动还没发布」）里点「保存并发布」带的就是
// 新视角，后端按新视角拒掉，跳转被中止，人留在原页而视角已经变了。
// 换视角放在下面守卫的最前头：守卫在离开拦截之后才跑，又得按新视角判断
// 去向——不然它会按旧视角自作主张换成另一个身份，新页面按那个身份加载。
// 拦截里选了留下，守卫根本不跑，视角原样不动
let pendingIdentity = ''
export async function switchIdentity(role) {
  pendingIdentity = role
  try {
    const failure = await router.push(role === 'student' ? '/square' : '/manage')
    // 已经在目标页上：没有离开这回事，守卫也不跑，直接换
    if (isNavigationFailure(failure, NavigationFailureType.duplicated)) setActiveIdentity(role)
  } finally {
    pendingIdentity = ''
  }
}

router.beforeEach((to) => {
  if (pendingIdentity) {
    setActiveIdentity(pendingIdentity)
    pendingIdentity = ''
  }
  // 统一身份认证回调要**最先**拦下来：CAS 回到的是根地址，不先转走的话，
  // 下面那条「没令牌就去登录页」会把人直接踢走，授权码白回来一趟。
  if (to.path !== '/sso-callback' && to.query.code) {
    return { path: '/sso-callback', query: to.query }
  }
  if (!to.meta.public && !auth.token) {
    return { path: '/login', query: { next: to.fullPath } }
  }
  const manageView = ['admin', 'initiator', 'qingxie'].includes(auth.activeIdentity)
  const home = manageView ? '/manage' : '/square'

  // 已登录再打开登录页（收藏的链接、后退回来）：不再登一遍，带 next 就去 next，
  // 否则去当前视角的首页。
  // signout 是注销自己发起的那一跳（见 MainLayout.onCommand）：那时令牌还没清
  // 掉——先离开当前页再注销，不然页面拦截里点「保存并发布」必定 401。不放它
  // 过去，这一跳会被重定向到首页，而重定向要以首页为新目标重走一遍导航，
  // 失活页面的离开拦截就跑第二遍，弹窗弹两次
  if (to.path === '/login' && auth.token && !to.query.signout) {
    const next = String(to.query.next || '')
    return /^\/(?!\/|login)/.test(next) ? next : home
  }

  // 首页按视角分流：工作台视角落地活动管理，学生视角落地活动广场
  if (to.path === '/') return home

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
    integrations: 'admin', feedback: 'admin', announcement: 'admin', oplogs: 'admin',
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
