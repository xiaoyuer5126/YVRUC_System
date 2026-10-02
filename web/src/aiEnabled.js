/** 智能起草开没开（后端配没配模型的 key，见 /api/auth/constants 的 ai_enabled）。
 *  整个页面会话里不会变，几处入口共用一次查询；查失败当没开，下次再查。 */
import { api } from './api'

let cache = null

export function aiEnabled() {
  cache ||= api.get('/api/auth/constants', { silent: true })
    .then(c => !!c.ai_enabled)
    .catch(() => { cache = null; return false })
  return cache
}
