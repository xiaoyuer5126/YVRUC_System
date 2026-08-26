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

export function download(url) {
  const a = document.createElement('a')
  a.href = url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(auth.token)
  a.click()
}
