/** 极简 fetch 封装：自动带 token、统一错误提示。 */
import { h } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { auth, clearAuth } from './store'
import { isEn, t } from './i18n'
import { router } from './router'

/** 令牌失效了：清掉本地登录态，把人送回登录页，登完再回到刚才那一页。
 *
 *  不清的话人卡在原地对着满屏「加载失败」发懵——路由守卫看的是「本地有没有
 *  令牌」，令牌过期或那个用户在库里已经没了（比如库被重置过）都不影响它放行，
 *  于是每个请求各自 401，一个红字接着一个红字。
 *
 *  多个请求同时 401 时只处理第一次：不然会连弹好几条提示、反复改地址。
 */
function sessionExpired() {
  if (!auth.token) return              // 已经处理过了
  clearAuth()
  ElMessage.warning(t('登录已过期，请重新登录'))
  const here = router.currentRoute.value.fullPath
  if (!here.startsWith('/login')) {
    router.replace({ path: '/login', query: here === '/' ? {} : { next: here } })
  }
}

/** 鉴权请求头：令牌 + 当前身份视角。
 *
 *  后端的权限跟着 X-Identity 走（只会收窄：自己没有的身份它不认）。每次请求
 *  现读 store，切了身份之后下一个请求就带新的，不必刷新页面。
 *  绕开 request() 自己 fetch 的地方（表单上传）也要用它，别只带令牌。
 */
export function authHeaders() {
  const headers = {}
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`
  if (auth.activeIdentity) headers['X-Identity'] = auth.activeIdentity
  return headers
}

async function request(method, url, body, opts = {}) {
  // 表单（带文件）原样发，Content-Type 让浏览器带上分隔符自己填
  const form = body instanceof FormData
  const headers = { ...(form ? {} : { 'Content-Type': 'application/json' }), ...authHeaders() }
  const resp = await fetch(url, {
    method,
    headers,
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  })
  const ct = resp.headers.get('content-type') || ''
  if (!ct.includes('application/json')) {
    if (!resp.ok) {
      // nginx 自己回的错误页（413 太大、502 后端没起来）不是 JSON，也要提示：
      // 不然表单点了提交毫无反应
      const msg = resp.status === 413 ? t('上传的文件太大了，请压缩或少传几份再试')
        : t('请求失败({0})', [resp.status])
      if (!opts.silent && !opts.skipErrorToast) ElMessage.error(msg)
      const err = new Error(msg)
      err.status = resp.status
      throw err
    }
    return resp
  }
  const data = await resp.json().catch(() => ({}))
  // 登录接口自己回的 401（口令错）不算「过期」：那时本来就在登录页上，
  // 交给下面按普通错误处理，把后端那句提示原样弹出来。
  if (resp.status === 401 && !url.startsWith('/api/auth/demo-login')) {
    sessionExpired()
    const e = new Error(t('登录已过期'))
    e.status = 401
    throw e
  }
  if (!resp.ok) {
    // 后端给的是中文原句；英文界面下按原句查译文，没收录的照原样显示中文。
    // 带活动名、时间的句子由后端拼好，不在这张表里，英文界面下也还是中文。
    const detail = typeof data.detail === 'string'
      ? t(data.detail)
      : (data.detail?.message ? t(data.detail.message)
         : (JSON.stringify(data.detail) || t('请求失败({0})', [resp.status])))
    // 时段冲突由调用方弹确认框（见 withConflictConfirm），这里不再报一遍错
    if (!opts.silent && !opts.skipErrorToast && data.detail?.code !== TIME_CONFLICT) {
      // 部门成员走完整个流程、在最后一步被拦下，这是设计好的学习权限，不是出错
      if (data.detail?.code === 'TRAINEE_READONLY') ElMessage.warning(detail)
      else ElMessage.error(detail)
    }
    const err = new Error(detail)
    err.status = resp.status
    err.data = data.detail
    throw err
  }
  return data
}

export const api = {
  get: (url, opts) => request('GET', url, undefined, opts),
  post: (url, body, opts) => request('POST', url, body, opts),
  put: (url, body, opts) => request('PUT', url, body, opts),
  patch: (url, body, opts) => request('PATCH', url, body, opts),
  del: (url, opts) => request('DELETE', url, undefined, opts),
}

const TIME_CONFLICT = 'time_conflict'

/** 冲突列表的一行：本活动的写「本活动 xx」，别的活动写活动名。纯文本。 */
export function conflictLine(c, activityId = '') {
  const where = activityId && c.activity_id === activityId
    ? '本活动 ' : `「${c.activity_title}」`
  return `${where}${c.shift_name}（${c.time}）${c.waiting ? '（候补中）' : ''}`
}

/** 后端回「时段冲突」（409，code=time_conflict）时先问一句，确认后带上
 *  confirm_conflict 重发。send(confirm) 发请求、返回 promise；取消返回 null，
 *  别的错误照常抛出。冲突只提示不硬拦，是不是真去不了由人自己判断。
 *
 *  head 是列表上方那句话，传 null 就用后端的 message（里面已写明哪几场撞了）。
 *  列表用 h() 拼成节点、文字走文本插值：活动名是别人填的，不能当 HTML 渲染。 */
export async function withConflictConfirm(send, { title = '时间冲突', ask = '仍要继续？',
                                                  head = '与以下场次时间冲突：',
                                                  activityId = '' } = {}) {
  try {
    return await send(false)
  } catch (e) {
    if (e?.data?.code !== TIME_CONFLICT) throw e
    const lines = (e.data.conflicts || []).map(c => conflictLine(c, activityId))
    try {
      await ElMessageBox.confirm(
        h('div', [
          h('p', { style: 'margin:0 0 8px' }, head ?? e.data.message),
          ...lines.map(l => h('div', { style: 'margin:4px 0' }, l)),
          h('p', { style: 'margin:8px 0 0' }, ask),
        ]),
        title, { type: 'warning', confirmButtonText: '仍要继续', cancelButtonText: '取消' })
    } catch {
      return null
    }
    return await send(true)
  }
}

const WEEKDAYS = { zh: '日一二三四五六', en: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] }

/** 后台给的本地时间串拆成 [年, 月, 日, 时, 分, 秒]，取不到的位是 null。
 *
 * 串形如 "2026-10-16T14:00:00" 或 "2026-10-16 14:00"，也可能只有日期。
 * 手工切字段，不用 `new Date(串)`——那会按浏览器的时区解释，把日期挪一天。
 */
function splitDT(iso) {
  const m = String(iso).match(/(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/)
  if (!m) return null
  return [m[1], m[2], m[3], m[4] ?? null, m[5] ?? null, m[6] ?? null]
}

/** 星期几。按年月日算，用 Date.UTC 取星期，不受本地时区影响。 */
function weekdayOf(y, mo, d) {
  const i = new Date(Date.UTC(+y, +mo - 1, +d)).getUTCDay()
  return isEn.value ? WEEKDAYS.en[i] : `周${WEEKDAYS.zh[i]}`
}

/** 「2026-10-16（周五）」的日期部分；英文界面下写成「2026-10-16 (Fri)」。 */
function dayText(y, mo, d) {
  const sep = isEn.value ? [' (', ')'] : ['（', '）']
  return `${y}-${mo}-${d}${sep[0]}${weekdayOf(y, mo, d)}${sep[1]}`
}

/** 带时间的日期：「2026-10-16（周五）14:00」；只给日期就是「2026-10-16（周五）」。 */
export function fmtDT(iso) {
  if (!iso) return '—'
  const p = splitDT(iso)
  if (!p) return '—'
  const day = dayText(p[0], p[1], p[2])
  return p[3] == null ? day : `${day}${p[3]}:${p[4]}`
}

/** 只要日期：「2026-10-16（周五）」。 */
export function fmtDate(iso) {
  if (!iso) return '—'
  const p = splitDT(iso)
  if (!p) return '—'
  return dayText(p[0], p[1], p[2])
}

/** 带秒的日期时间：「2026-10-16（周五）14:00:05」。操作日志、审计日志用。 */
export function fmtDTs(iso) {
  if (!iso) return '—'
  const p = splitDT(iso)
  if (!p) return '—'
  const day = dayText(p[0], p[1], p[2])
  return p[3] == null ? day : `${day}${p[3]}:${p[4]}:${p[5] ?? '00'}`
}

/** 窄处用的日期时间，省掉星期但保住年份：「2026-10-16 14:00」。
 *
 * 手机和桌面共用的窄格子放不下「（周五）」时用它；拿不准就用 fmtDT。
 */
export function fmtDTShort(iso) {
  if (!iso) return '—'
  const p = splitDT(iso)
  if (!p) return '—'
  const day = `${p[0]}-${p[1]}-${p[2]}`
  return p[3] == null ? day : `${day} ${p[3]}:${p[4]}`
}

/** 站内页面的对外网址，二维码与分享链接都用它。
 *
 * path 形如 '/checkin/AB12CD'。取当前浏览器地址作为前缀：这是管理者此刻
 * 打开的地址，也正是要让志愿者扫到的地址。别让后端按请求头去推——
 * cloudflared 到 nginx 之间是明文 http，nginx 还把 X-Forwarded-Proto
 * 覆写成了 $scheme，推出来会是个 http:// 的错网址。
 */
export function pageUrl(path) {
  return `${window.location.origin}${path}`
}

/** 二维码接口的 base 参数（已编码）。 */
export function qrBase() {
  return encodeURIComponent(window.location.origin)
}

/** 直链下载（导出、学习材料）。
 *
 * <a> 发不出 Authorization 头，所以先用带头的请求换一张票据，
 * 再把票据挂在地址上。换票请求带着当前身份，票上记着它，下载时照此判权。票只对这一个网址有效、一分钟——
 * 会话令牌不会再出现在地址栏、浏览器历史和服务器访问日志里。
 */
export async function download(url) {
  const path = url.split('?')[0]
  let ticket
  try {
    ({ ticket } = await api.post('/api/auth/download-ticket', { path }))
  } catch {
    return          // 换票失败，api 层已经提示过，不必再抛
  }
  const a = document.createElement('a')
  a.href = url + (url.includes('?') ? '&' : '?') + 'ticket=' + encodeURIComponent(ticket)
  a.click()
}

/** 导出表格；本次导出带问卷里上传的文件时，再问要不要把附件包一起下。
 *
 * base 形如 /api/staff/activities/<id>/answers：表是 base.xlsx，附件数是
 * base/attachments，附件包是 base/attachments.zip，三处都带上同一个 query
 * （筛选条件）。表和附件分开下——多数时候只要表，不必每次拖着几十兆的附件。
 * 附件数单独问一次 JSON 接口，而不是放在表的响应头里：直链下载是 <a> 点出去的，
 * 前端读不到响应头。先问数目再下表，没权限之类的错在这一步就能提示出来。
 */
export async function downloadExport(base, query = '') {
  const q = query ? `?${query}` : ''
  let count
  try {
    ({ count } = await api.get(`${base}/attachments${q}`))
  } catch {
    return          // api 层已经提示过
  }
  await download(`${base}.xlsx${q}`)
  if (!count) return
  try {
    await ElMessageBox.confirm(`本次导出含 ${count} 个附件，是否同时打包下载？`, '附件', {
      confirmButtonText: '打包下载', cancelButtonText: '不用了', type: 'info',
    })
  } catch {
    return
  }
  await download(`${base}/attachments.zip${q}`)
}

/** 上传一个文件（multipart）。FormData 不能走 api 的 JSON 封装，错误提示照旧。 */
export async function uploadFile(url, file) {
  const form = new FormData()
  form.append('file', file)
  const resp = await fetch(url, {
    method: 'POST', body: form,
    headers: authHeaders(),
  })
  const data = await resp.json().catch(() => ({}))
  if (resp.status === 401) sessionExpired()
  if (!resp.ok) {
    const detail = typeof data.detail === 'string' ? t(data.detail) : t('上传失败({0})', [resp.status])
    if (data.detail?.code === 'TRAINEE_READONLY') ElMessage.warning(data.detail.message)
    else ElMessage.error(detail)
    throw new Error(detail)
  }
  return data
}
