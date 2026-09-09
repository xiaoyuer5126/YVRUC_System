/** 发了新版之后提醒页面刷新。
 *
 * 前端换了版本，已经开着的页面手上还是旧代码：新功能看不见，接口改了还会
 * 直接报错，而人只会以为平台坏了。构建时把构建号同时编进 bundle 和 dist 根
 * 下的 version.json，页面每隔十分钟、以及重新切回前台时比一次，不一样就弹窗。
 *
 * 用挡住页面的弹窗而不是角落里的小提示：旧版页面继续用下去会出错，这事得
 * 让人看见。但刷不刷由人决定——有人可能正填着报名问卷，页面一跳就白填了。
 */
import { ElMessageBox } from 'element-plus'

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
  try {
    await ElMessageBox.confirm(
      '平台已经发布了新版本，当前页面还是旧的。刷新即可换上新版；'
      + '页面上没提交的内容会丢失，可以填完再刷新。',
      '平台已更新',
      { confirmButtonText: '立即刷新', cancelButtonText: '稍后再说',
        type: 'info', closeOnClickModal: false })
  } catch {
    return          // 选「稍后」就不再打扰，下次进平台自然是新版
  }
  location.reload()
}

export function watchVersion() {
  if (import.meta.env.DEV) return   // 开发服务器自己热更新，不用比
  setInterval(check, INTERVAL)
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check() })
}
