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
  if (roles.includes('initiator')) return 'initiator'
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
  admin: '管理员', qingxie: '青协', initiator: '活动发起方', student: '学生',
}

export const hasIdentity = (role) => !!auth.user && (auth.user.roles || []).includes(role)
export const isAdmin = () => hasIdentity('admin')
export const isManager = () =>
  isAdmin() || hasIdentity('initiator') || hasIdentity('qingxie')

// 青协层级：会长团 / 部长 / 部门骨干 / 部门成员。只在青协视角下有意义
export const post = () => (auth.user?.qingxie_post || '')
const qx = (...posts) => auth.activeIdentity === 'qingxie' && posts.includes(post())
// 管理员视角，或会长团视角：除集成设置外同权
const full = () => auth.activeIdentity === 'admin' || qx('council')
export const isTrainee = () => auth.activeIdentity === 'qingxie' && post() === 'member'

export const POST_LABELS = { council: '会长团', head: '部长', core: '部门骨干', member: '部门成员' }

// —— 页面可见性（按当前身份视角） ——
export const canSee = {
  dashboard: () => full(),
  manage: () => ['admin', 'initiator', 'qingxie'].includes(auth.activeIdentity),
  audit: () => ['admin', 'initiator', 'qingxie'].includes(auth.activeIdentity),
  users: () => full() || qx('head'),
  roles: () => full() || qx('head'),
  approvals: () => full() || qx('head'),
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

// 日期区间选择器的默认时刻：先点日期时，起止会预填成当天的第一刻与最后一刻，
// 省得每次都手动把结束时间从 00:00 拖到 23:59
export const RANGE_DEFAULT_TIME = [
  new Date(2000, 0, 1, 0, 0, 0),
  new Date(2000, 0, 1, 23, 59, 59),
]
export const DAY_END = new Date(2000, 0, 1, 23, 59, 59)
