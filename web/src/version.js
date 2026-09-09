/** 发了新版之后提醒页面刷新。
 *
 * 前端换了版本，已经开着的页面手上还是旧代码：新功能看不见，接口改了还会
 * 直接报错，而人只会以为平台坏了。构建时把构建号同时编进 bundle 和 dist 根
 * 下的 version.json，页面每隔十分钟、以及重新切回前台时比一次，不一样就提示。
 *
 * 只提示不自动刷新：有人可能正填着报名问卷，页面一跳就白填了。
 */
import { ElNotification } from 'element-plus'

const INTERVAL = 10 * 60 * 1000
let notified = false

async function check() {
  if (notified || document.hidden) return
  try {
    const r = await fetch(`${import.meta.env.BASE_URL}version.json`, { cache: 'no-store' })
    if (!r.ok) return
    const { build } = await r.json()
    if (!build || build === __BUILD_ID__) return
  } catch {
    return          // 断网、后端在重启：下一轮再比，不打扰
  }
  notified = true
  ElNotification({
    title: '平台已更新',
    message: '点这条消息刷新页面换上新版本；没提交的内容会丢失，可以填完再刷新。',
    type: 'info', duration: 0, position: 'bottom-right',
    onClick: () => location.reload(),
  })
}

export function watchVersion() {
  if (import.meta.env.DEV) return   // 开发服务器自己热更新，不用比
  setInterval(check, INTERVAL)
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check() })
}
