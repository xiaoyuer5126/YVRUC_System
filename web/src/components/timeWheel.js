/** 时分滚轮：给日期时间面板换掉 Element 自带的那两列。
 *
 * Element 的自带滚轮是一条可滚动长列表：没有吸附、没有惯性、小时拨到头会顶住，
 * 慢慢拨时还会被弹回原位。这里换成循环 + 吸附的滚轮，尺寸照搬它
 * （每格 32、字 12px、面板 180×192 + 36 的按钮条），换上去位置看不出变化。
 *
 * 用原生 DOM 写、不依赖 Vue：这个节点要插进 Element 组件自己的 DOM 里，
 * 交给 Vue 渲染会和它的更新互相覆盖。
 */

const H = 32 // 每格高度，和 Element 的 el-time-spinner__item 一样
const RANGE = 3 // 中间上下各画几格
const DECAY = 0.84 // 松手后的衰减，越小停得越快
const FLY_MAX = 22 // 松手最多再滑多少格/秒
const NUDGE = 0.32 // 慢慢划时，过了这么多格就算要走一格

const CSS = `
.dtw {
  position: absolute; inset: 0; z-index: 3;
  display: flex; flex-direction: column;
  background: var(--el-bg-color-overlay, #fff); border-radius: inherit;
}
.dtw-cols { position: relative; flex: 1; display: flex; }
.dtw-band {
  position: absolute; left: 12%; right: 12%; top: 50%; height: ${H}px;
  margin-top: -${H / 2}px; pointer-events: none;
  border-top: 1px solid var(--el-border-color-light, #e4e7ed);
  border-bottom: 1px solid var(--el-border-color-light, #e4e7ed);
}
.dtw-col {
  position: relative; width: 50%; height: 100%; overflow: hidden;
  perspective: 620px; touch-action: none; cursor: grab; user-select: none;
  -webkit-mask-image: linear-gradient(to bottom, transparent 0%, #000 16%, #000 84%, transparent 100%);
  mask-image: linear-gradient(to bottom, transparent 0%, #000 16%, #000 84%, transparent 100%);
}
.dtw-col.is-dragging { cursor: grabbing; }
.dtw-tick {
  position: absolute; left: 50%; top: 50%; width: 100%;
  transform-origin: center center; text-align: center;
  height: ${H}px; line-height: ${H}px; font-size: 12px;
  color: var(--el-text-color-regular, #606266);
  font-variant-numeric: tabular-nums;
  will-change: transform, opacity;
}
.dtw-tick.is-on { color: var(--el-text-color-primary, #303133); font-weight: 700; }
.dtw-foot {
  box-sizing: border-box; height: 36px; padding: 4px;
  border-top: 1px solid var(--el-border-color-light, #e4e7ed);
  display: flex; align-items: center; justify-content: flex-end;
}
.dtw-btn {
  background: transparent; border: 0; outline: none; cursor: pointer;
  color: var(--el-text-color-primary, #303133);
  font: inherit; font-size: 12px; line-height: 28px; padding: 0 5px; margin: 0 5px;
}
.dtw-ok { color: var(--el-color-primary, #409eff); font-weight: 800; }
`

let cssInjected = false
function injectCss() {
  if (cssInjected) return
  cssInjected = true
  const style = document.createElement('style')
  style.dataset.dtw = ''
  style.textContent = CSS
  document.head.appendChild(style)
}

const pad = n => String(n).padStart(2, '0')

/** 一列循环滚轮。值和位置都是「第几格」，外面再乘成时分。 */
class Column {
  constructor(count, value, onChange) {
    this.count = count
    this.pos = value
    this.target = null
    this.targetSet = false
    this.vel = 0
    this.dragging = false
    this.moved = 0
    this.prevDy = 0
    this.gesturing = false
    this.gestureStart = 0
    this.gestureCells = 0
    this.onChange = onChange
    this.lastValue = Math.round(value)
    this.alive = true

    this.el = document.createElement('div')
    this.el.className = 'dtw-col'
    this.ticks = []
    for (let i = 0; i < RANGE * 2 + 1; i++) {
      const tick = document.createElement('div')
      tick.className = 'dtw-tick'
      this.el.appendChild(tick)
      this.ticks.push(tick)
    }

    this.el.addEventListener('pointerdown', e => this.down(e))
    this.el.addEventListener('pointermove', e => this.move(e))
    this.el.addEventListener('pointerup', e => this.up(e))
    this.el.addEventListener('pointercancel', e => this.up(e))
    this.el.addEventListener('wheel', e => this.wheel(e), { passive: false })

    this.last = performance.now()
    requestAnimationFrame(t => this.frame(t))
    this.render()
  }

  /** 循环：任何整数都落到 0..count-1 */
  indexOf(k) {
    const c = this.count
    return ((k % c) + c) % c
  }

  render() {
    const base = Math.round(this.pos)
    for (let i = 0; i < this.ticks.length; i++) {
      const el = this.ticks[i]
      const k = base + (i - RANGE)
      const y = (k - this.pos) * H
      const t = y / H
      el.textContent = pad(this.indexOf(k))
      el.style.transform = `translate(-50%, -50%) translateY(${y.toFixed(2)}px)`
        + ` rotateX(${Math.max(-72, Math.min(72, -t * 22)).toFixed(2)}deg)`
        + ` scale(${Math.max(0.62, 1 - Math.abs(t) * 0.1).toFixed(3)})`
      el.style.opacity = String(Math.max(0.1, 1 - Math.abs(t) * 0.28))
      el.classList.toggle('is-on', Math.abs(t) < 0.5)
    }
  }

  commitIfChanged() {
    const v = this.indexOf(Math.round(this.pos))
    if (v !== this.lastValue) {
      this.lastValue = v
      this.onChange(v)
    }
  }

  frame(now) {
    if (!this.alive) return
    const dt = Math.min(48, now - this.last)
    this.last = now
    if (!this.dragging) {
      let moved = false
      if (this.targetSet && this.target !== null) {
        const d = this.target - this.pos
        if (Math.abs(d) < 0.004) {
          this.pos = this.target
          this.targetSet = false
        } else {
          this.pos += d * Math.min(1, dt / 70)
        }
        moved = true
      } else if (Math.abs(this.vel) > 0.008) {
        this.pos += (this.vel * dt) / H
        this.vel *= Math.pow(DECAY, dt / 16)
        if (Math.abs(this.vel) <= 0.008) this.vel = 0
        moved = true
      }
      if (!this.gesturing && this.vel === 0 && !this.targetSet) {
        const t = Math.round(this.pos)
        const d = t - this.pos
        if (Math.abs(d) > 0.0015) {
          this.pos += d * Math.min(1, dt / 60)
          moved = true
        } else if (this.pos !== t) {
          this.pos = t
          moved = true
        }
      }
      if (moved) {
        this.render()
        this.commitIfChanged()
      }
    }
    requestAnimationFrame(t => this.frame(t))
  }

  down(e) {
    this.dragging = true
    this.vel = 0
    this.targetSet = false
    this.moved = 0
    this.prevDy = 0
    this.startY = e.clientY
    this.startPos = this.pos
    this.samples = [{ t: performance.now(), y: e.clientY }]
    this.el.classList.add('is-dragging')
    try {
      this.el.setPointerCapture(e.pointerId)
    } catch (_) {
      // 老浏览器没有指针捕获，拖动仍然可用
    }
    e.preventDefault()
  }

  move(e) {
    if (!this.dragging) return
    const dy = e.clientY - this.startY
    this.moved += Math.abs(dy - this.prevDy)
    this.prevDy = dy
    this.pos = this.startPos - dy / H
    this.render()
    this.commitIfChanged()
    this.samples.push({ t: performance.now(), y: e.clientY })
    if (this.samples.length > 6) this.samples.shift()
    e.preventDefault()
  }

  up(e) {
    if (!this.dragging) return
    this.dragging = false
    this.el.classList.remove('is-dragging')

    // 几乎没动就是点了一下：把点到的格子拨到中间
    if (this.moved < 3) {
      const rect = this.el.getBoundingClientRect()
      const offset = e.clientY - (rect.top + rect.height / 2)
      this.target = Math.round(this.pos) + Math.round(offset / H)
      this.targetSet = true
      this.vel = 0
      this.samples = []
      return
    }

    const s = this.samples || []
    this.vel = 0
    if (s.length >= 2) {
      const a = s[0]
      const b = s[s.length - 1]
      const dt = Math.max(1, b.t - a.t)
      if (dt < 160) {
        const v = (-(b.y - a.y) / dt) * 16
        this.vel = Math.max(-FLY_MAX, Math.min(FLY_MAX, Math.abs(v) < 0.7 ? 0 : v))
      }
    }
    this.samples = []

    // 慢慢划、没过半格时，过了微调门槛也走一格
    if (this.vel === 0) {
      const startK = Math.round(this.startPos)
      const delta = this.pos - this.startPos
      let target = Math.round(this.pos)
      if (target === startK && Math.abs(delta) >= NUDGE) {
        target = startK + Math.sign(delta)
      }
      if (target !== Math.round(this.pos) || Math.abs(this.pos - target) > 0.002) {
        this.target = target
        this.targetSet = true
      }
    }
  }

  /** 触摸板上两指划是一串很密的小事件；鼠标滚轮一次给一大格。
   * 一串小事件当成一次手势：中间不吸附，停 150ms 后才定，否则会被每帧的
   * 吸附拽回去，看着就是卡住不动。 */
  wheel(e) {
    e.preventDefault()
    let d = e.deltaY
    if (e.deltaMode === 1) d *= 16
    if (e.deltaMode === 2) d *= 400

    if (!this.gesturing) {
      this.gesturing = true
      this.gestureStart = this.pos
      this.gestureCells = 0
      this.targetSet = false
      this.vel = 0
    }

    if (Math.abs(d) >= 40) {
      const notches = Math.max(1, Math.round(Math.abs(d) / 100))
      this.gestureCells += Math.sign(d) * notches
      this.pos = Math.round(this.gestureStart) + this.gestureCells
    } else {
      this.pos += (d / H) * 0.85
    }
    this.render()
    this.commitIfChanged()

    clearTimeout(this.wheelTimer)
    this.wheelTimer = setTimeout(() => this.endWheel(), 150)
  }

  endWheel() {
    this.gesturing = false
    const startK = Math.round(this.gestureStart)
    const delta = this.pos - this.gestureStart
    let target = Math.round(this.pos)
    if (target === startK && Math.abs(delta) >= NUDGE) {
      target = startK + Math.sign(delta)
    }
    this.target = target
    this.targetSet = true
  }

  get value() {
    return this.indexOf(Math.round(this.pos))
  }

  setValue(v) {
    this.pos = v
    this.vel = 0
    this.targetSet = false
    this.lastValue = v
    this.render()
  }

  destroy() {
    this.alive = false
    clearTimeout(this.wheelTimer)
  }
}

/** 造一个滚轮面板。
 * minutes 是起始时刻（一天的分钟数），onConfirm 收到确定时的分钟数，
 * onCancel 收到取消。返回值里的 el 直接塞进 .el-time-panel。 */
export function createTimeWheel({ minutes, onConfirm, onCancel }) {
  injectCss()

  const hour = new Column(24, Math.floor(minutes / 60), () => {})
  const minute = new Column(60, minutes % 60, () => {})

  const root = document.createElement('div')
  root.className = 'dtw'

  const cols = document.createElement('div')
  cols.className = 'dtw-cols'
  const band = document.createElement('div')
  band.className = 'dtw-band'
  cols.appendChild(band)
  cols.appendChild(hour.el)
  cols.appendChild(minute.el)

  const foot = document.createElement('div')
  foot.className = 'dtw-foot'
  const cancelBtn = document.createElement('button')
  cancelBtn.type = 'button'
  cancelBtn.className = 'dtw-btn'
  cancelBtn.textContent = '取消'
  const okBtn = document.createElement('button')
  okBtn.type = 'button'
  okBtn.className = 'dtw-btn dtw-ok'
  okBtn.textContent = '确定'
  foot.append(cancelBtn, okBtn)

  // 面板里的点击不要冒到外面去，否则会被当成点到了别处
  root.addEventListener('pointerdown', e => e.stopPropagation())

  cancelBtn.addEventListener('click', onCancel)
  okBtn.addEventListener('click', () => onConfirm(hour.value * 60 + minute.value))

  root.append(cols, foot)

  return {
    el: root,
    destroy() {
      hour.destroy()
      minute.destroy()
    },
  }
}
