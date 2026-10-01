// 各视口下的页面自检：横向溢出、侧栏/汉堡切换、页面有没有真的渲染出来。
//
// 用法（栈已起好、种子数据已导入）：
//   python3 scripts/viewcheck-setup.py /tmp/viewcheck
//   node scripts/viewcheck.mjs /tmp/viewcheck 390 844     # 手机
//   node scripts/viewcheck.mjs /tmp/viewcheck 1440 900    # 桌面回归
//
// 为什么同时看「文字长度」和「JS 错误」：崩掉的页面是空白的，
// 而空白页永远不会横向溢出——只看溢出会把整页报错判成通过：
// 漏一个 import 整页白屏，纯溢出检查照样给通过。
//
// 「JS 错误」要连 console.error 一起收：Vue 会把 setup 里抛的异常接住、
// 用 console.error 打出来，页面照常渲染，window.onerror 一声不吭。
// 一个子组件 setup 崩了，父级拿到的就是个空壳，点按钮才报「不是函数」。
// 所以活动管理页的每个页签都要打开一遍，子组件只在自己的页签里挂载。
import { spawn } from 'node:child_process'
import { readFileSync } from 'node:fs'
const CHROME = '/home/xiaoyuer5126/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome'
const S = process.argv[2], W = +process.argv[3], H = +process.argv[4]
const ACT = readFileSync(`${S}/act.txt`, 'utf8')
const REC = readFileSync(`${S}/record_act.txt`, 'utf8')
const ATT = readFileSync(`${S}/attire_act.txt`, 'utf8')
const ATT_TOKEN = readFileSync(`${S}/attire_token.txt`, 'utf8')
const CHK_TOKEN = readFileSync(`${S}/checkin_token.txt`, 'utf8')
// [身份, 路由, 名称, 这一页至少该渲染出多少字]
// 最少字数不是凑数：活动管理页的「找不到活动」也是一屏内容，只看「有没有
// 东西」分不出正常与出错——接口换了返回结构，所有页签都成了这张页，检查
// 照样全绿。按「这一页该有的量」比，空壳就露馅了。
const PAGES = [
  ['student', '/square', '活动广场', 100],
  ['student', `/activity/${ACT}`, '活动详情', 200],
  ['student', '/my', '我的报名', 100],
  ['student', '/hours', '志愿时长', 50],
  ['student', '/messages', '消息通知', 10],
  ['student', `/checkin/${CHK_TOKEN}`, '签到落地', 20],
  ['student', `/attire/${ATT_TOKEN}`, '服装落地', 20],
  ['admin', '/manage', '活动管理', 200],
  ['admin', `/manage/${ACT}?tab=tree`, '场次岗位', 200],
  ['admin', `/manage/${ACT}?tab=roster`, '志愿者管理', 200],
  ['admin', `/manage/${ACT}?tab=checkin`, '签到管理', 200],
  ['admin', `/manage/${ACT}?tab=hours`, '时长管理', 150],
  ['admin', `/manage/${ACT}?tab=acks`, '签收情况', 100],
  ['admin', `/manage/${ACT}?tab=review`, '审核', 100],
  ['admin', `/manage/${ACT}?tab=managers`, '管理者', 100],
  ['admin', `/manage/${ACT}?tab=logs`, '变更记录', 100],
  ['admin', `/manage/${ATT}?tab=attire`, '志愿服装', 100],
  ['admin', `/manage/${REC}?tab=records`, '名单导入', 100],
  ['admin', '/users', '志愿者档案', 200],
  ['admin', '/approvals', '活动审批', 200],
  ['admin', '/audit', '审计日志', 200],
  ['admin', '/dashboard', '数据总览', 200],
  ['admin', '/roles', '角色管理', 200],
  ['admin', '/integrations', '集成设置', 100],
  ['admin', '/feedback', '意见反馈', 20],
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
// 每个调用限时：页面主线程卡死时浏览器不再应答，不限时的话脚本会一声不吭地
// 一直挂着，看不出是哪一页出了事。超时就当这一页出错报出来，后面的页照常检查
const TIMEOUT_MS = 10000
const send=(method,params={})=>new Promise(res=>{const i=++id
  const tm=setTimeout(()=>{pend.delete(i);res({timeout:true})},TIMEOUT_MS)
  pend.set(i,m=>{clearTimeout(tm);res(m)});ws.send(JSON.stringify({id:i,method,params}))})
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
    // 崩掉的页面不会溢出，所以必须同时看「有没有渲染出东西」。
    // 签到/服装落地页不在主框架里，没有 .app-main，就看整页
    textLen: ((document.querySelector('.app-main') || document.body).innerText || '').trim().length,
    errors: window.__errs || [],
    vw, scrollW: document.documentElement.scrollWidth,
    aside: !!document.querySelector('.side'),
    burger: !!document.querySelector('.hamburger'),
    cards: document.querySelectorAll('.cards .card, .mob-card, .m-shift').length,
    tables: document.querySelectorAll('.el-table').length,
    over,
  });
})()`

// 每次导航都注入一次：收 window 错误、未处理的 Promise，以及 console.error /
// 带 deprecat 或 [Vue warn] 的 console.warn（Vue 把 setup 的异常接住后就是这么打的）
await send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {
  window.__errs = [];
  addEventListener('error', e => window.__errs.push(String(e.message)));
  addEventListener('unhandledrejection', e => window.__errs.push(String(e.reason)));
  const ce = console.error.bind(console);
  console.error = (...a) => { window.__errs.push(a.map(String).join(' ').slice(0, 200)); ce(...a) };
  const cw = console.warn.bind(console);
  console.warn = (...a) => {
    const t = a.map(String).join(' ');
    if (/deprecat|\\[Vue warn\\]/i.test(t)) window.__errs.push(t.slice(0, 200));
    cw(...a);
  };
})()` })

for (const [who, route, name, min] of PAGES) {
  await send('Page.navigate', { url: 'http://127.0.0.1:8080/' })
  await sleep(700)
  await send('Runtime.evaluate', { expression: `
    localStorage.setItem('rucva_token', ${JSON.stringify(TOK[who])});
    localStorage.setItem('rucva_user', ${JSON.stringify(USR[who])});
    localStorage.setItem('rucva_active_identity', ${JSON.stringify(who)});
    location.assign(${JSON.stringify(route)});` })
  await sleep(2400)
  const r = await send('Runtime.evaluate', { expression: PROBE, returnByValue: true })
  const d = r.timeout
    ? { textLen: 0, errors: ['页面没有响应（主线程卡死？）'], vw: 0, scrollW: 0, over: [] }
    : JSON.parse(r.result.result.value)
  const bad = d.scrollW > d.vw + 1 || d.textLen < min || d.errors.length
  console.log(`${bad ? '✗' : '✓'} ${name.padEnd(6)} 视口${d.vw} 内容${d.scrollW}` +
    ` | 侧栏${d.aside?'在':'收'} 汉堡${d.burger?'有':'无'}` +
    ` | 卡片${d.cards} 表格${d.tables} 文字${d.textLen}` +
    (d.textLen < min ? `（少于该有的 ${min}）` : '') +
    (d.errors.length ? `\n     JS 错误: ${d.errors[0]}` : '') +
    (d.over.length ? `\n     超宽: ${d.over.join(', ')}` : ''))
}
ws.close(); proc.kill()
