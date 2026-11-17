// 页面切换动画（View Transitions，同文档的 document.startViewTransition）。
//
// 只是锦上添花：浏览器没有这个接口（iOS 16 及以下的 iPhone、老内核的安卓微信）、
// 或者系统开了「减少动态效果」，就照常直接换页，不加 polyfill。直接调这个接口
// 而不先判断，不支持的浏览器上会抛错，整次跳转停在旧页面上。
//
// 挂在 beforeResolve 上：它在所有守卫和懒加载组件都过完之后才跑。页面的离开
// 拦截（「改动还没发布」那种确认框）在它之前，确认框弹着的时候画面不会被冻住；
// 被守卫重定向走的那一趟也到不了这里，只有最终落地的那一趟播动画。
//
// 效果分两层：
// - 整页淡入淡出（根元素的默认 crossfade）。顶栏和侧栏在切换期间单独起名、
//   不播动画，看上去是外框不动、只有内容区在换。
// - 列表 ↔ 详情的「共享元素」：被点的那张卡片（或那一行）平滑变成详情页的
//   页头，返回时反过来。两边各用 data-vt-morph="act-<活动 id>" 标出可以对上
//   的元素，data-vt-part 标出是整块（box）还是标题（title）。名字只在切换的
//   这一下临时设上：同一个 view-transition-name 在页面上出现两次，浏览器会
//   直接放弃这次动画，所以平时谁都不带名字。
import { nextTick } from 'vue'

const PARTS = ['box', 'title']

// 列表页与详情页成对出现：从列表进详情、从详情回列表都做共享元素变形
const PAIRS = [
  [/^\/square$/, /^\/activity\/([^/]+)$/],
  [/^\/manage$/, /^\/manage\/([^/]+)$/],
]

// 详情页的页头要等接口回来才渲染。最多等这么久，过了就只做淡入淡出：
// 等待期间画面停在旧页面的快照上，等太久会显得点了没反应
const MORPH_WAIT_MS = 400
// 兜底：新页面怎么也渲染不出来时，最多冻住这么久就放手
const UPDATE_CAP_MS = 1500

function supported() {
  return typeof document.startViewTransition === 'function'
    && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function morphKey(from, to) {
  for (const [list, detail] of PAIRS) {
    let m
    if (list.test(from.path) && (m = detail.exec(to.path))) return 'act-' + m[1]
    if (list.test(to.path) && (m = detail.exec(from.path))) return 'act-' + m[1]
  }
  return ''
}

// 给页面上 key 对应的元素临时起名，返回起了名的元素，结束后好清掉
function nameMorph(key) {
  const named = []
  for (const part of PARTS) {
    const el = document.querySelector(`[data-vt-morph="${key}"][data-vt-part="${part}"]`)
    // 只给排成一个框的元素起名：display:none 的拍不出快照，折成两行的行内
    // 元素（长标题）有好几个框，浏览器也不拿它做变形
    if (el && el.getClientRects().length === 1) {
      el.style.viewTransitionName = 'vt-' + part
      named.push(el)
    }
  }
  return named
}

function waitForMorph(key) {
  const sel = `[data-vt-morph="${key}"]`
  if (document.querySelector(sel)) return Promise.resolve()
  return new Promise((resolve) => {
    const t0 = Date.now()
    // 切换期间页面不重绘，requestAnimationFrame 不一定会来，用定时器轮询
    const tick = () => {
      if (document.querySelector(sel) || Date.now() - t0 >= MORPH_WAIT_MS) resolve()
      else setTimeout(tick, 16)
    }
    setTimeout(tick, 16)
  })
}

// 手机上手势返回（iOS 右滑、安卓预测式返回）时浏览器自己已经播了一段过场，
// 再叠一层就是两段动画。浏览器在 popstate 上用 hasUAVisualTransition 说明这件事
let uaTransition = false
window.addEventListener('popstate', (e) => { uaTransition = !!e.hasUAVisualTransition }, true)

export function installViewTransitions(router) {
  // 正在等新页面渲染的那一次切换：{ key, finish }
  let pending = null

  router.beforeResolve((to, from) => {
    const ua = uaTransition
    uaTransition = false
    // 首次打开（from 还没有匹配的路由）、同一页只改查询串（筛选、页签）不播
    if (!supported() || ua || !from.matched.length || to.path === from.path) return
    pending?.finish()

    const key = morphKey(from, to)
    const root = document.documentElement
    return new Promise((proceed) => {
      root.classList.add('vt-nav')
      const named = key ? nameMorph(key) : []
      let release
      const updated = new Promise((r) => { release = r })
      const me = {
        key,
        named,
        finish() {
          if (pending === me) pending = null
          release()
        },
      }
      pending = me
      setTimeout(me.finish, UPDATE_CAP_MS)
      // 回调在旧页面拍完快照后才被调用：这时才放导航继续走，新页面渲染完、
      // afterEach 里 finish 之后，浏览器再拍新页面、开始播动画
      const vt = document.startViewTransition(() => {
        proceed()
        return updated
      })
      vt.finished.finally(() => {
        root.classList.remove('vt-nav')
        for (const el of me.named) el.style.viewTransitionName = ''
      })
    })
  })

  router.afterEach(async (to, from, failure) => {
    const me = pending
    if (!me) return
    if (!failure) {
      await nextTick()
      if (me.key) {
        await waitForMorph(me.key)
        me.named.push(...nameMorph(me.key))
      }
    }
    me.finish()
  })
}
