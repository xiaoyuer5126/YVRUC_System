/** 报名问卷文件题的限制：大小、个数、格式（/api/auth/constants 的 answer_file）。
 *  数字以后端为准，这里不抄第二份；出题页和答题区共用一次查询，查失败下次再查。 */
import { api } from '../api'

let cache = null

/** { max_mb, max_count, total_mb, exts }；查不到时为 null */
export function answerFileRules() {
  cache ||= api.get('/api/auth/constants', { silent: true })
    .then(c => c.answer_file || null)
    .catch(() => { cache = null; return null })
  return cache
}

/** 给文件选择框的 accept：.pdf,.mp4,... */
export const acceptOf = rules => (rules?.exts || []).map(e => `.${e}`).join(',')
