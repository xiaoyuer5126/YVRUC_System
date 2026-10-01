/** 极简 fetch 封装：自动带 token、统一错误提示。 */
import { ElMessage, ElMessageBox } from 'element-plus'
import { auth, clearAuth } from './store'
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
  ElMessage.warning('登录已过期，请重新登录')
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
  const headers = { 'Content-Type': 'application/json', ...authHeaders() }
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
  if (resp.status === 401) {
    // 登录接口自己回的 401（口令错）不算「过期」，那时本来就在登录页上
    sessionExpired()
    const e = new Error('登录已过期')
    e.status = 401
    throw e
  }
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
 * <a> 发不出 Authorization 头，所以先用带头的请求换一张一次性票据，
 * 再把票据挂在地址上。换票请求带着当前身份，票上记着它，下载时照此判权。票只对这一个网址有效、一分钟、用一次即作废——
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
 * base 形如 /api/staff/activities/<id>/export：表是 base.xlsx，附件数是
 * base/attachments，附件包是 base/attachments.zip。表和附件分开下——多数
 * 时候只要表，不必每次拖着几十兆的附件。
 * 附件数单独问一次 JSON 接口，而不是放在表的响应头里：直链下载是 <a> 点出去的，
 * 前端读不到响应头。先问数目再下表，没权限之类的错在这一步就能提示出来。
 */
export async function downloadExport(base) {
  let count
  try {
    ({ count } = await api.get(`${base}/attachments`))
  } catch {
    return          // api 层已经提示过
  }
  await download(`${base}.xlsx`)
  if (!count) return
  try {
    await ElMessageBox.confirm(`本次导出含 ${count} 个附件，是否同时打包下载？`, '附件', {
      confirmButtonText: '打包下载', cancelButtonText: '不用了', type: 'info',
    })
  } catch {
    return
  }
  await download(`${base}/attachments.zip`)
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
    const detail = typeof data.detail === 'string' ? data.detail : `上传失败(${resp.status})`
    if (data.detail?.code === 'TRAINEE_READONLY') ElMessage.warning(data.detail.message)
    else ElMessage.error(detail)
    throw new Error(detail)
  }
  return data
}
