import { reactive } from 'vue'

export const auth = reactive({
  token: localStorage.getItem('yvruc_token') || '',
  user: JSON.parse(localStorage.getItem('yvruc_user') || 'null'),
  // 当前身份视角（在持有的身份之间切换；决定菜单与默认落地页）
  activeIdentity: localStorage.getItem('yvruc_active_identity') || '',
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
  localStorage.setItem('yvruc_token', token)
  localStorage.setItem('yvruc_user', JSON.stringify(user))
  localStorage.setItem('yvruc_active_identity', auth.activeIdentity)
}

export function setActiveIdentity(role) {
  auth.activeIdentity = role
  localStorage.setItem('yvruc_active_identity', role)
}

// 登录后落在哪个视角：管理员进管理端；青协只有会长团和部长默认进青协视角，
// 骨干和成员平时就是来报名的学生，要办事再自己切
function pickDefaultIdentity(user) {
  const roles = user?.roles || []
  if (roles.includes('admin')) return 'admin'
  if (roles.includes('qingxie') && ['council', 'head'].includes(user.qingxie_post)) return 'qingxie'
  if (roles.includes('initiator')) return 'initiator'
  return 'student'
}

export function clearAuth() {
  auth.token = ''
  auth.user = null
  auth.activeIdentity = ''
  localStorage.removeItem('yvruc_token')
  localStorage.removeItem('yvruc_user')
  localStorage.removeItem('yvruc_active_identity')
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
  volunteers: () => full() || qx('head'),
  roles: () => full() || qx('head', 'core'),   // 骨干进来只为认领本部门成员
  // 对外联络部的骨干有值班资格：要看得到谁在班、轮到自己时接得了班、处理得了审批
  approvals: () => full() || qx('head') || (qx('core') && !!auth.user?.duty_eligible),
  integrations: () => auth.activeIdentity === 'admin',
  feedback: () => auth.activeIdentity === 'admin',   // 意见反馈的处理端；提交入口人人都有
  personal: () => true,
  pin: () => full(),          // 置顶急招：全校层面的调度，会长团/管理员
}

// 颜色是前端的事；名称与释义以后端 /api/auth/constants 为准，别在这里抄第二份
export const MODE_COLORS = {
  instant: { color: '#67c23a' },     // 即选即得
  screened: { color: '#409eff' },    // 报名后筛选
  record: { color: '#909399' },      // 不公开招募（补记录）
}

// 只点了日期没点时刻时补的默认时刻：开始给上班点，截止给当天最后一分钟，
// 省得每次都手动把截止时间从 00:00 拖到 23:59
export const DAY_START = new Date(2000, 0, 1, 8, 0, 0)
export const DAY_END = new Date(2000, 0, 1, 23, 59, 59)
