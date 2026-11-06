/** 把 Element 日期时间面板里的时分滚轮换成自己的循环滚轮。
 *
 * Element 的日期时间面板没有开放替换滚轮的插槽。这里不重画面板，只做两件事：
 * 1. 在它弹出的 .el-time-panel 上盖一层我们自己的滚轮，尺寸完全照抄它
 *    （内容 192 高、按钮条 36、宽 180），所以位置看不出变化；
 * 2. 把原生那两列和它自己的按钮条设成 visibility:hidden，保留占位。
 *
 * 选值回填走 Element 自己的路径：它的「时间」输入框认手打的 HH:mm，
 * 写进去再触发 change，它就跟平时人打字一样更新、收起面板、emit 新值。
 * 这样日历、取消、确定、此刻、表单校验全都不用碰。
 *
 * 依赖的内部类名（Element 2.9～2.14 都在，大版本升级后要回来核一次）：
 *   .el-time-panel                    时间面板，每次打开都新建一个
 *   .el-time-panel__content / __footer 里面要藏起来的两块
 *   .el-date-picker__editor-wrap      顶部两个输入框，第二个是时间
 *   .el-time-spinner__item.is-active  原生滚轮当前停在哪个数
 */

const HIDE_STYLE_ID = 'dtw-hide-native'

function ensureHideStyle() {
  if (document.getElementById(HIDE_STYLE_ID)) return
  const style = document.createElement('style')
  style.id = HIDE_STYLE_ID
  // 只藏不删：原生的内容还在，面板尺寸就还是原来的尺寸
  style.textContent = `
    .el-time-panel .el-time-panel__content,
    .el-time-panel .el-time-panel__footer {
      visibility: hidden;
    }
  `
  document.head.appendChild(style)
}

const pad = n => String(n).padStart(2, '0')

/** 从原生滚轮读当前停在哪个时、哪个分；读不到就看顶部时间框里写的 */
function readNativeTime(panel, popper) {
  const active = panel.querySelectorAll('.el-time-spinner__item.is-active')
  const hour = active[0] ? Number(active[0].textContent) : NaN
  const minute = active[1] ? Number(active[1].textContent) : NaN
  if (Number.isFinite(hour) && Number.isFinite(minute)) return { hour, minute }

  const wraps = popper.querySelectorAll('.el-date-picker__editor-wrap')
  const input = wraps[1] && wraps[1].querySelector('input')
  const m = input && /^(\d{1,2}):(\d{1,2})/.exec(input.value.trim())
  return m ? { hour: Number(m[1]), minute: Number(m[2]) } : { hour: 0, minute: 0 }
}

/** 把 HH:mm 写进顶部那个时间输入框，再触发它自己的 change */
function applyTime(popper, minutes) {
  const wraps = popper.querySelectorAll('.el-date-picker__editor-wrap')
  const input = wraps[1] && wraps[1].querySelector('input')
  if (!input) return
  const text = `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`
  // 用原生 setter 写，免得被取值拦截挡住
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
  setter.call(input, text)
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.dispatchEvent(new Event('change', { bubbles: true }))
}

// 全站只挂一个监听：每多一个时间控件就多挂一个，页面上会同时有十来个
const factories = new Map()
let observer = null
let installed = new WeakMap() // panel → wheel，用来在面板被移除时停掉它的动画
let seq = 0

function handlePanel(panel) {
  if (installed.has(panel)) return

  // 往上找这个面板所在的弹层，再看弹层上挂的是哪个控件的类名
  let node = panel.parentElement
  let popper = null
  let factory = null
  while (node && node !== document.body) {
    for (const [cls, fn] of factories) {
      if (node.classList.contains(cls)) {
        popper = node
        factory = fn
        break
      }
    }
    if (factory) break
    node = node.parentElement
  }
  if (!popper || !factory) return

  const { hour, minute } = readNativeTime(panel, popper)
  const start = hour * 60 + minute

  const wheel = factory({
    minutes: start,
    onConfirm: mins => applyTime(popper, mins),
    // 取消也走同一条路，但写回原值：面板收起、值不变
    onCancel: () => applyTime(popper, start),
  })
  panel.appendChild(wheel.el)
  installed.set(panel, wheel)
}

function scan() {
  document.querySelectorAll('.el-time-panel').forEach(handlePanel)
}

function ensureObserver() {
  if (observer) return
  observer = new MutationObserver(records => {
    let added = false
    for (const rec of records) {
      for (const node of rec.addedNodes) {
        if (node.nodeType === 1 && (node.matches('.el-time-panel') || node.querySelector('.el-time-panel'))) {
          added = true
        }
      }
      for (const node of rec.removedNodes) {
        if (node.nodeType !== 1) continue
        // 面板关了：停掉滚轮的动画循环
        const panels = node.matches('.el-time-panel')
          ? [node]
          : Array.from(node.querySelectorAll('.el-time-panel'))
        for (const p of panels) {
          const wheel = installed.get(p)
          if (wheel) {
            wheel.destroy()
            installed.delete(p)
          }
        }
      }
    }
    if (added) scan()
  })
  observer.observe(document.body, { childList: true, subtree: true })
  scan()
}

/** 注册一个控件。createTimeWheel 是造滚轮的函数，返回卸载函数。 */
export function mountTimeWheel(createTimeWheel) {
  ensureHideStyle()
  const cls = `dtw-${++seq}`
  factories.set(cls, createTimeWheel)
  ensureObserver()

  return {
    popperClass: cls,
    unmount() {
      factories.delete(cls)
      if (factories.size === 0 && observer) {
        observer.disconnect()
        observer = null
      }
    },
  }
}
