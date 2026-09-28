/** 带 token 的文件上传。fetch 封装（api.js）只走 JSON，表单文件在这里单独发。 */
import { authHeaders } from '../api'

export async function uploadTo(url, file) {
  const fd = new FormData()
  fd.append('file', file)
  const resp = await fetch(url, { method: 'POST', headers: authHeaders(), body: fd })
  const d = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    throw new Error(typeof d.detail === 'string' ? d.detail : `上传失败(${resp.status})`)
  }
  return d
}

/** 上传一张图片，返回可直接放进 <img> 的地址（联络群二维码用）。 */
export async function uploadImage(file) {
  return (await uploadTo('/api/staff/upload-image', file)).url
}
