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
    if (!opts.silent && !opts.skipErrorToast) ElMessage.error(detail)
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

export function download(url) {
  const a = document.createElement('a')
  a.href = url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(auth.token)
  a.click()
}
