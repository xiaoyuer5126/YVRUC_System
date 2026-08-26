import { reactive } from 'vue'

export const auth = reactive({
  token: localStorage.getItem('rucva_token') || '',
  user: JSON.parse(localStorage.getItem('rucva_user') || 'null'),
  // 当前身份视角（在持有的身份之间切换；决定菜单与默认落地页）
  activeIdentity: localStorage.getItem('rucva_active_identity') || '',
})

// 自校验：本地残留的视角不在当前用户持有的身份里（如换号登录、身份被调整）→ 回退默认
if (auth.user && auth.activeIdentity &&
    !(auth.user.roles || []).includes(auth.activeIdentity)) {
  auth.activeIdentity = ''
}

export function setAuth(token, user) {
  auth.token = token
  auth.user = user
  auth.activeIdentity = pickDefaultIdentity(user)
  localStorage.setItem('rucva_token', token)
  localStorage.setItem('rucva_user', JSON.stringify(user))
  localStorage.setItem('rucva_active_identity', auth.activeIdentity)
}

export function setActiveIdentity(role) {
  auth.activeIdentity = role
  localStorage.setItem('rucva_active_identity', role)
}

function pickDefaultIdentity(user) {
  const roles = user?.roles || []
  if (roles.includes('admin')) return 'admin'
  if (roles.includes('teacher')) return 'teacher'
  if (roles.includes('qingxie')) return 'qingxie'
  return 'student'
}

export function clearAuth() {
  auth.token = ''
  auth.user = null
  auth.activeIdentity = ''
  localStorage.removeItem('rucva_token')
  localStorage.removeItem('rucva_user')
  localStorage.removeItem('rucva_active_identity')
}

export const ROLE_LABELS = {
  admin: '管理员', teacher: '老师', qingxie: '青协', student: '学生',
}

export const hasIdentity = (role) => !!auth.user && (auth.user.roles || []).includes(role)
export const isAdmin = () => hasIdentity('admin')
export const isManager = () =>
  isAdmin() || hasIdentity('teacher') || hasIdentity('qingxie')

// —— 页面可见性（按当前身份视角） ——
export const canSee = {
  dashboard: () => auth.activeIdentity === 'admin',
  manage: () => ['admin', 'teacher', 'qingxie'].includes(auth.activeIdentity),
  audit: () => ['admin', 'teacher', 'qingxie'].includes(auth.activeIdentity),
  users: () => ['admin', 'qingxie'].includes(auth.activeIdentity),
  integrations: () => auth.activeIdentity === 'admin',
  personal: () => true,
}

// 颜色是前端的事；名称与释义以后端 /api/auth/constants 为准，别在这里抄第二份
export const TYPE_COLORS = {
  A: { label: 'A类·常规单次', color: '#67c23a' },
  B: { label: 'B类·常规长期', color: '#409eff' },
  C: { label: 'C类·大型校级', color: '#e6a23c' },
  D: { label: 'D类·重大活动', color: '#f56c6c' },
}
