// 各视口下的页面自检：横向溢出、侧栏/汉堡切换、页面有没有真的渲染出来。
//
// 用法（栈已起好、种子数据已导入）：
//   python3 scripts/viewcheck-setup.py /tmp/viewcheck
//   node scripts/viewcheck.mjs /tmp/viewcheck 390 844     # 手机
//   node scripts/viewcheck.mjs /tmp/viewcheck 1440 900    # 桌面回归
//
// 为什么同时看「文字长度」和「JS 错误」：崩掉的页面是空白的，
// 而空白页永远不会横向溢出——只看溢出会把整页报错判成通过。
// 这不是假设，是本次适配时真的发生过：漏了一个 import，
// 「我的报名」整页白屏，纯溢出检查照样给了通过。
import { spawn } from 'node:child_process'
import { readFileSync } from 'node:fs'
const CHROME = '/home/xiaoyuer5126/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome'
const S = process.argv[2], W = +process.argv[3], H = +process.argv[4]
const PAGES = [
  ['student', '/square', '活动广场'],
  ['student', `/activity/${readFileSync(`${S}/act.txt`, 'utf8')}`, '活动详情'],
  ['student', '/my', '我的报名'],
  ['student', '/credit', '信用分'],
  ['student', '/hours', '志愿时长'],
  ['student', '/messages', '消息通知'],
  ['admin', '/manage', '活动管理'],
  ['admin', '/users', '用户与信用'],
  ['admin', '/audit', '审计日志'],
  ['admin', '/dashboard', '数据总览'],
  ['admin', '/roles', '角色管理'],
]
const TOK = { student: readFileSync(`${S}/tok.txt`,'utf8'), admin: readFileSync(`${S}/atok.txt`,'utf8') }
const USR = { student: readFileSync(`${S}/suser.json`,'utf8'), admin: readFileSync(`${S}/auser.json`,'utf8') }
const port = 9334
const proc = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`,
  '--no-sandbox','--disable-gpu','--hide-scrollbars','about:blank'], { stdio:'ignore' })
const sleep = ms => new Promise(r => setTimeout(r, ms))
await sleep(1800)
const t = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(x=>x.type==='page')
const ws = new WebSocket(t.webSocketDebuggerUrl)
await new Promise(r => ws.addEventListener('open', r))
let id=0; const pend=new Map()
ws.addEventListener('message', e=>{ const m=JSON.parse(e.data); if(m.id&&pend.has(m.id)){pend.get(m.id)(m);pend.delete(m.id)} })
const send=(method,params={})=>new Promise(res=>{const i=++id;pend.set(i,res);ws.send(JSON.stringify({id:i,method,params}))})
await send('Page.enable')
await send('Emulation.setDeviceMetricsOverride',{width:W,height:H,deviceScaleFactor:2,mobile:true})

const PROBE = `(() => {
  const vw = document.documentElement.clientWidth;
  const over = [...document.querySelectorAll('body *')]
    .filter(el => { const r = el.getBoundingClientRect();
                    return r.width > 0 && r.right > vw + 1.5; })
    .map(el => (el.tagName.toLowerCase() +
       (el.className && typeof el.className === 'string'
          ? '.' + el.className.trim().split(/\\s+/).slice(0,2).join('.') : '')
       + ' →' + Math.round(el.getBoundingClientRect().right)))
    .filter((v,i,a) => a.indexOf(v) === i).slice(0, 6);
  return JSON.stringify({
    // 崩掉的页面不会溢出，所以必须同时看「有没有渲染出东西」
    textLen: (document.querySelector('.app-main')?.innerText || '').trim().length,
    errors: window.__errs || [],
    vw, scrollW: document.documentElement.scrollWidth,
    aside: !!document.querySelector('.side'),
    burger: !!document.querySelector('.hamburger'),
    cards: document.querySelectorAll('.cards .card, .mob-card, .m-shift').length,
    tables: document.querySelectorAll('.el-table').length,
    over,
  });
})()`

for (const [who, route, name] of PAGES) {
  await send('Page.navigate', { url: 'http://127.0.0.1:8080/' })
  await sleep(700)
  await send('Page.addScriptToEvaluateOnNewDocument', { source:
    "window.__errs=[];addEventListener('error',e=>window.__errs.push(String(e.message)));" +
    "addEventListener('unhandledrejection',e=>window.__errs.push(String(e.reason)));" })
  await send('Runtime.evaluate', { expression: `
    localStorage.setItem('rucva_token', ${JSON.stringify(TOK[who])});
    localStorage.setItem('rucva_user', ${JSON.stringify(USR[who])});
    localStorage.setItem('rucva_active_identity', ${JSON.stringify(who)});
    location.hash = '#${route}'; location.reload();` })
  await sleep(2400)
  const r = await send('Runtime.evaluate', { expression: PROBE, returnByValue: true })
  const d = JSON.parse(r.result.result.value)
  const bad = d.scrollW > d.vw + 1 || d.textLen < 40 || d.errors.length
  console.log(`${bad ? '✗' : '✓'} ${name.padEnd(6)} 视口${d.vw} 内容${d.scrollW}` +
    ` | 侧栏${d.aside?'在':'收'} 汉堡${d.burger?'有':'无'}` +
    ` | 卡片${d.cards} 表格${d.tables} 文字${d.textLen}` +
    (d.errors.length ? `\n     JS 错误: ${d.errors[0]}` : '') +
    (d.over.length ? `\n     超宽: ${d.over.join(', ')}` : ''))
}
ws.close(); proc.kill()
