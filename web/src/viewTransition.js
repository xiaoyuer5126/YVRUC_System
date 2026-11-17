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
//   动画不等接口：详情页的页头要等接口回来才有内容，等的这段时间画面冻在旧页面
//   的快照上，看着就是点了没反应。所以列表页在跳转前把被点的活动交给 peek，
//   详情页数据还没到时先用它画出页头，变形当场就有对象。
//
// 内容区的滚动位置也在这里管：页面在 .app-main 里滚，不是整个窗口在滚，
// 浏览器和路由都不替它记位置。不管的话换页时滚动条原样留着，点进详情页
// 落在半中间。进新页面滚回顶部，后退、前进回到离开时的位置。
import { nextTick, shallowRef } from 'vue'

const PARTS = ['box', 'title']

// 列表页与详情页成对出现：从列表进详情、从详情回列表都做共享元素变形
const PAIRS = [
  [/^\/square$/, /^\/activity\/([^/]+)$/],
  [/^\/manage$/, /^\/manage\/([^/]+)$/],
]

// 兜底：新页面怎么也渲染不出来时，最多冻住这么久就放手
const UPDATE_CAP_MS = 1000
// 后退回列表时，列表要等接口回来才够高，滚动位置得跟着内容长高再补；最多跟这么久
const RESTORE_FOLLOW_MS = 1500

// —— 列表页交给详情页的活动（只用来先画页头） ——
const peek = shallowRef(null)
export function peekActivity(a) { peek.value = a }
// 当前详情页的 id 对得上才用，id 取值函数如 () => route.params.id
export function peekFor(id) {
  const p = peek.value
  return p && String(p.id) === String(id()) ? p : null
}

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

// —— 内容区滚动位置 ——
const scrollBox = () => document.querySelector('.app-main')
const savedScroll = new Map()     // fullPath → 离开时的 scrollTop
let stopFollow = () => {}

function applyScroll(to, back) {
  stopFollow()
  const el = scrollBox()
  if (!el) return
  const top = back ? savedScroll.get(to.fullPath) || 0 : 0
  el.scrollTop = top
  if (el.scrollTop >= top - 1) return
  // 页面还不够高：内容长出来一次就再滚一次，到位、超时或用户自己动了就停
  const mo = new MutationObserver(() => {
    el.scrollTop = top
    if (el.scrollTop >= top - 1) stopFollow()
  })
  const timer = setTimeout(() => stopFollow(), RESTORE_FOLLOW_MS)
  const userMoved = () => stopFollow()
  stopFollow = () => {
    mo.disconnect()
    clearTimeout(timer)
    el.removeEventListener('wheel', userMoved)
    el.removeEventListener('touchstart', userMoved)
    stopFollow = () => {}
  }
  mo.observe(el, { childList: true, subtree: true })
  el.addEventListener('wheel', userMoved, { passive: true })
  el.addEventListener('touchstart', userMoved, { passive: true })
}

// popstate 在路由守卫之前同步触发：记下这一趟是后退/前进，以及浏览器是不是
// 已经自己播了过场（iOS 右滑、安卓预测式返回）。后者再叠一层就是两段动画
let popNav = false
let uaTransition = false
window.addEventListener('popstate', (e) => {
  popNav = true
  uaTransition = !!e.hasUAVisualTransition
}, true)

export function installViewTransitions(router) {
  // 正在等新页面渲染的那一次切换：{ key, named, finish }
  let pending = null

  router.beforeResolve((to, from) => {
    const ua = uaTransition
    uaTransition = false
    if (to.path !== from.path && from.matched.length) {
      const el = scrollBox()
      if (el) savedScroll.set(from.fullPath, el.scrollTop)
    }
    // 首次打开（from 还没有匹配的路由）、同一页只改查询串（筛选、页签）不播
    if (!supported() || ua || !from.matched.length || to.path === from.path) return
    pending?.finish()

    const key = morphKey(from, to)
    const root = document.documentElement
    return new Promise((proceed) => {
      root.classList.add('vt-nav')
      let release
      const updated = new Promise((r) => { release = r })
      const me = {
        key,
        named: key ? nameMorph(key) : [],
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
    const back = popNav
    popNav = false
    const me = pending
    if (!failure && to.path !== from.path) {
      await nextTick()
      // 滚动要在新页面拍快照之前定好，不然动画播完画面又跳一下
      applyScroll(to, back)
      if (me?.key) me.named.push(...nameMorph(me.key))
    }
    me?.finish()
  })
}
