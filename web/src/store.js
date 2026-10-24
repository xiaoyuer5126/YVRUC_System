import { reactive } from 'vue'

// 当前身份视角只记在本标签页（sessionStorage）：刷新时沿用刚才切到的视角，
// 重新打开页面则回到按身份算出的默认视角。要是记在 localStorage，青协同学
// 某次点通知进了个人页、被切到学生视角，之后每次打开都落在学生视角，
// 工作台得自己再切回去
const IDENTITY_KEY = 'yvruc_active_identity'
// 平台公告只在登录时弹一次：登录成功打上这个标记，主框架弹过就清掉。
// 放 sessionStorage：登录完刷新页面还没看到也照样弹，开新标签页不会再弹
const ANNOUNCE_KEY = 'yvruc_announce_pending'
const storedUser = JSON.parse(localStorage.getItem('yvruc_user') || 'null')

export const auth = reactive({
  token: localStorage.getItem('yvruc_token') || '',
  user: storedUser,
  // 当前身份视角（在持有的身份之间切换；决定菜单与默认落地页）
  activeIdentity: '',
})

// 本标签页切到过、且仍持有的视角就沿用；否则（新开页面、换号登录、身份被调整）
// 按身份取默认
const storedIdentity = sessionStorage.getItem(IDENTITY_KEY) || ''
auth.activeIdentity = storedUser && (storedUser.roles || []).includes(storedIdentity)
  ? storedIdentity
  : (storedUser ? pickDefaultIdentity(storedUser) : '')

export function setAuth(token, user) {
  auth.token = token
  auth.user = user
  auth.activeIdentity = pickDefaultIdentity(user)
  localStorage.setItem('yvruc_token', token)
  localStorage.setItem('yvruc_user', JSON.stringify(user))
  sessionStorage.setItem(IDENTITY_KEY, auth.activeIdentity)
  sessionStorage.setItem(ANNOUNCE_KEY, '1')
}

/** 这次登录还没弹过公告：返回 true 并清掉标记，同一次登录只弹一次。 */
export function takeAnnouncementTurn() {
  if (!sessionStorage.getItem(ANNOUNCE_KEY)) return false
  sessionStorage.removeItem(ANNOUNCE_KEY)
  return true
}

export function setActiveIdentity(role) {
  auth.activeIdentity = role
  sessionStorage.setItem(IDENTITY_KEY, role)
}

// 默认落在哪个视角：管理员进管理端；青协的会长团、部长、骨干默认进青协视角，
// 部门成员平时就是来报名的学生，要学着办事再自己切
function pickDefaultIdentity(user) {
  const roles = user?.roles || []
  if (roles.includes('admin')) return 'admin'
  if (roles.includes('qingxie') && ['council', 'head', 'core'].includes(user.qingxie_post)) return 'qingxie'
  if (roles.includes('initiator')) return 'initiator'
  return 'student'
}

export function clearAuth() {
  auth.token = ''
  auth.user = null
  auth.activeIdentity = ''
  localStorage.removeItem('yvruc_token')
  localStorage.removeItem('yvruc_user')
  sessionStorage.removeItem(IDENTITY_KEY)
  sessionStorage.removeItem(ANNOUNCE_KEY)
}

export const ROLE_LABELS = {
  admin: '管理员', qingxie: '青协', initiator: '活动发起方', student: '志愿者',
}

export const hasIdentity = (role) => !!auth.user && (auth.user.roles || []).includes(role)
export const isAdmin = () => hasIdentity('admin')
export const isManager = () =>
  isAdmin() || hasIdentity('initiator') || hasIdentity('qingxie')

// 青协层级：会长团 / 部长 / 部门骨干 / 部门成员。只在青协视角下有意义
export const post = () => (auth.user?.qingxie_post || '')
const qx = (...posts) => auth.activeIdentity === 'qingxie' && posts.includes(post())
// 管理员视角，或会长团视角：除平台公告、操作日志、意见反馈外同权
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
  announcement: () => auth.activeIdentity === 'admin',
  oplogs: () => auth.activeIdentity === 'admin',      // 会长团不行
  notifyLog: () => auth.activeIdentity === 'admin',   // 会长团不行
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
