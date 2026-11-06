/** 智能起草的前端小工具。 */
import { api } from './api'

let cache = null

/** 开没开（后端配没配模型的 key，见 /api/auth/constants 的 ai_enabled）。
 *  整个页面会话里不会变，几处入口共用一次查询；查失败当没开，下次再查。 */
export function aiEnabled() {
  cache ||= api.get('/api/auth/constants', { silent: true })
    .then(c => !!c.ai_enabled)
    .catch(() => { cache = null; return false })
  return cache
}

/** 起草请求的表单：原文 + 附件（File 对象）。 */
export function aiForm(text, files = []) {
  const form = new FormData()
  form.append('text', text || '')
  for (const f of files) form.append('files', f)
  return form
}

// 附件：格式与数量和后端（config.AI_FILE_*）一致
export const AI_FILE_ACCEPT = '.docx,.xlsx,.pdf,.png,.jpg,.jpeg,.webp,.gif'
export const AI_FILE_LIMIT = 5
export const AI_FILE_TIP = '可选，docx、xlsx、pdf 或图片，最多 5 个、单个不超过 10 MB'
