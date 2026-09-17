/** 极简 fetch 封装：自动带 token、统一错误提示。 */
import { ElMessage } from 'element-plus'
import { auth } from './store'

async function request(method, url, body, opts = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`
  const resp = await fetch(url, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const ct = resp.headers.get('content-type') || ''
  if (!ct.includes('application/json')) {
    if (!resp.ok) throw new Error(`请求失败(${resp.status})`)
    return resp
  }
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    const detail = typeof data.detail === 'string'
      ? data.detail
      : (data.detail?.message || JSON.stringify(data.detail) || `请求失败(${resp.status})`)
    if (!opts.silent && !opts.skipErrorToast) {
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

export function fmtDT(iso) {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(5, 16)
}

/** 站内页面的对外网址，二维码与分享链接都用它。
 *
 * hash 形如 '/checkin/AB12CD'。取当前浏览器地址作为前缀：这是管理者此刻
 * 打开的地址，也正是要让志愿者扫到的地址。别让后端按请求头去推——
 * cloudflared 到 nginx 之间是明文 http，nginx 还把 X-Forwarded-Proto
 * 覆写成了 $scheme，推出来会是个 http:// 的错网址。
 */
export function pageUrl(hash) {
  return `${window.location.origin}${window.location.pathname}#${hash}`
}

/** 二维码接口的 base 参数（已编码）。 */
export function qrBase() {
  return encodeURIComponent(`${window.location.origin}${window.location.pathname}`)
}

/** 直链下载（导出、学习材料）。
 *
 * <a> 发不出 Authorization 头，所以先用带头的请求换一张一次性票据，
 * 再把票据挂在地址上。票只对这一个网址有效、一分钟、用一次即作废——
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
