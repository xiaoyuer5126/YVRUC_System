// 真实浏览器截图/交互工具（CDP 直连，无第三方依赖）
// 用法：
//   node scripts/shot.mjs <url> <out.png> [等待毫秒] [要点击的CSS选择器(可选，多个用 | 分隔)]
// 依赖：本机 Edge；Node >= 22（内置 WebSocket）
import { spawn } from 'node:child_process'
import { writeFileSync } from 'node:fs'

const [, , url, out, waitMsArg, clickSelectors] = process.argv
if (!url || !out) {
  console.error('usage: node shot.mjs <url> <out.png> [waitMs] [sel1|sel2]')
  process.exit(1)
}
const waitMs = Number(waitMsArg || 8000)
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
const PORT = 9223

const edge = spawn(EDGE, [
  '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
  `--remote-debugging-port=${PORT}`, '--window-size=1500,950',
  '--user-data-dir=' + process.env.TEMP + '\\rucva_edge_profile',
  'about:blank',
], { stdio: 'ignore' })

const sleep = ms => new Promise(r => setTimeout(r, ms))

async function getWsUrl() {
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/version`)
      const j = await res.json()
      return j.webSocketDebuggerUrl
    } catch { await sleep(300) }
  }
  throw new Error('CDP not reachable')
}

const ws = new WebSocket(await getWsUrl())
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j })

let id = 0
const pending = new Map()
const events = []
ws.onmessage = ev => {
  const msg = JSON.parse(ev.data)
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id)
    pending.delete(msg.id)
    msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
  } else if (msg.method) {
    events.push(msg)
  }
}
function send(method, params = {}, sessionId) {
  return new Promise((resolve, reject) => {
    const mid = ++id
    pending.set(mid, { resolve, reject })
    ws.send(JSON.stringify({ id: mid, method, params, sessionId }))
  })
}

const { targetId } = await send('Target.createTarget', { url: 'about:blank' })
const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true })
await send('Page.enable', {}, sessionId)
await send('Runtime.enable', {}, sessionId)
await send('Page.navigate', { url }, sessionId)
await sleep(waitMs)

// 可选：依次执行动作。支持 text=按钮文字:N / eval=<js表达式> / CSS选择器
if (clickSelectors) {
  for (const act of clickSelectors.split('|').filter(Boolean)) {
    let expr
    if (act.startsWith('eval=')) {
      expr = act.slice(5)
    } else if (act.startsWith('text=')) {
      const m = act.slice(5).match(/^(.*?)(?::(\d+))?$/)
      const txt = m[1]
      const nth = Number(m[2] || 1)
      expr = `(() => {
        const btns = [...document.querySelectorAll('button, .el-button')]
          .filter(b => (b.textContent || '').trim().includes(${JSON.stringify(txt)}))
        const el = btns[${nth - 1}]
        if (!el) return 'NOT_FOUND:' + ${JSON.stringify(act)} + ' (matches=' + btns.length + ')'
        el.click(); return 'CLICKED[' + ${nth} + '/' + btns.length + ']:' + el.textContent.trim().slice(0, 20)
      })()`
    } else {
      expr = `(() => {
        const el = document.querySelector(${JSON.stringify(act)})
        if (!el) return 'NOT_FOUND:' + ${JSON.stringify(act)}
        el.click(); return 'CLICKED:' + ${JSON.stringify(act)}
      })()`
    }
    const r = await send('Runtime.evaluate', {
      expression: expr, returnByValue: true, awaitPromise: true,
    }, sessionId)
    console.log(r.result.value)
    await sleep(1200)
  }
}

// 收集页面报错（debug-login.html 会把错误写进 #log）
const logs = await send('Runtime.evaluate', {
  expression: `document.getElementById('log') ? document.getElementById('log').textContent : ''`,
  returnByValue: true,
}, sessionId)
if (logs.result.value && logs.result.value.includes('ERROR')) {
  console.log('PAGE LOGS:', logs.result.value.split('\n').filter(l => l.includes('ERROR')).join('\n'))
}

const shot = await send('Page.captureScreenshot', { format: 'png' }, sessionId)
writeFileSync(out, Buffer.from(shot.data, 'base64'))
console.log('saved:', out)
ws.close()
edge.kill()
process.exit(0)
