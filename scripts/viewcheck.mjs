// 各视口下的页面自检：横向溢出、侧栏/汉堡切换、页面有没有真的渲染出来、
// 同类元素的间距齐不齐。
//
// 用法（栈已起好、种子数据已导入；Node ≥ 22，要用内置 WebSocket）：
//   python3 scripts/viewcheck-setup.py /tmp/viewcheck
//   node scripts/viewcheck.mjs /tmp/viewcheck 390 844     # 手机
//   node scripts/viewcheck.mjs /tmp/viewcheck 1440 900    # 桌面回归
//
// 浏览器取环境变量 CHROME，没设就用下面写死的本机路径。有一页出 ✗ 退出码就是 1，
// 出 ✗ 的页面截图存到 <输出目录>/shots/。PR 改到 web/、backend/、scripts/ 时，
// CI 的「页面自检」会自动跑这两遍，截图和输出在那次运行的附件里。
//
// 为什么同时看「文字长度」和「JS 错误」：崩掉的页面是空白的，
// 而空白页永远不会横向溢出——只看溢出会把整页报错判成通过：
// 漏一个 import 整页白屏，纯溢出检查照样给通过。
//
// 「JS 错误」要连 console.error 一起收：Vue 会把 setup 里抛的异常接住、
// 用 console.error 打出来，页面照常渲染，window.onerror 一声不吭。
// 一个子组件 setup 崩了，父级拿到的就是个空壳，点按钮才报「不是函数」。
// 所以活动管理页的每个页签都要打开一遍，子组件只在自己的页签里挂载。
//
// 「间距不齐」只比同类的东西：同一排的控件（按钮、标签、输入框，或只包着
// 一个控件的壳），同一栏里上下相邻的块（有边框或底色的卡片、提示条）。
// 标题与正文、区块与区块本来就隔得不一样，不比。这类问题肉眼要盯着看才觉得
// 「哪里怪」，截图里几乎看不出来：Element 给相邻两个按钮自带 12px 左外边距，
// 和 flex 的 gap 叠成 20px，被 tooltip 的 span 包住的那个按钮又只有 8px。
import { spawn } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
const CHROME = process.env.CHROME
  || '/home/xiaoyuer5126/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome'
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
  ['admin', '/volunteers', '志愿者档案', 200],
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
  '--no-sandbox','--disable-gpu','--hide-scrollbars','about:blank'], { stdio:['ignore','ignore','inherit'] })
const sleep = ms => new Promise(r => setTimeout(r, ms))
// 浏览器起来要多久看机器：本机两秒内，CI 上更久。轮询到调试端口应答为止，最多 30 秒
let t
for (let i = 0; !t && i < 60; i++) {
  await sleep(500)
  t = await fetch(`http://127.0.0.1:${port}/json/list`)
    .then(r => r.json()).then(l => l.find(x => x.type === 'page')).catch(() => undefined)
}
if (!t) { console.error(`浏览器 30 秒内没起来：${CHROME}`); proc.kill(); process.exit(1) }
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
  const vis = el => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden'
      && cs.position !== 'absolute' && cs.position !== 'fixed'; };
  const shown = el => [...el.children].filter(vis);
  const CTRL = 'button.el-button:not(.is-link):not(.is-text), .el-tag, .el-input, '
             + '.el-select, .el-switch, .el-date-editor, .el-radio-group, .el-checkbox';
  const isCtrl = el => el.matches(CTRL) || (shown(el).length === 1 && isCtrl(shown(el)[0]));
  const boxy = (el, d = 0) => {
    const cs = getComputedStyle(el);
    if ((parseFloat(cs.borderTopWidth) > 0 && parseFloat(cs.borderBottomWidth) > 0)
        || (!/^(rgba\\(0, 0, 0, 0\\)|transparent)$/.test(cs.backgroundColor)
            && el.getBoundingClientRect().height > 30)) return true;
    const k = shown(el);
    return d < 2 && k.length > 0 && boxy(k[0], d + 1) && boxy(k[k.length - 1], d + 1);
  };
  const label = el => (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 8) || el.tagName.toLowerCase();
  // gaps: [[前一个, 后一个, 间距]]；40px 以上是 margin-left:auto 之类有意推开的，不算
  const uneven = gaps => { const g = gaps.filter(x => x[2] >= 0 && x[2] < 40).map(x => x[2]);
    return g.length >= 2 && Math.max(...g) - Math.min(...g) >= 4; };
  const fmt = gaps => gaps.map(([a, b, g]) => label(a) + ' ‹' + g + '› ' + label(b)).join(' ｜ ');
  const gapsOff = [];
  for (const p of (document.querySelector('.app-main') || document.body).querySelectorAll('*')) {
    if (p.closest('.el-table, .el-select-dropdown, .el-picker-panel, .el-radio-group')) continue;
    const kids = shown(p);
    if (kids.length < 3) continue;
    const R = new Map(kids.map(k => [k, k.getBoundingClientRect()]));
    const rows = [];
    for (const k of kids) { const cy = R.get(k).top + R.get(k).height / 2;
      let row = rows.find(x => Math.abs(x.cy - cy) < 6);
      if (!row) rows.push(row = { cy, items: [] });
      row.items.push(k); }
    for (const row of rows) {
      const it = row.items.sort((a, b) => R.get(a).left - R.get(b).left);
      const gaps = it.slice(1).map((b, n) => [it[n], b, Math.round(R.get(b).left - R.get(it[n]).right)])
        .filter(([a, b]) => isCtrl(a) && isCtrl(b));
      if (uneven(gaps)) gapsOff.push('横排 ' + fmt(gaps));
    }
    const col = [];
    for (const k of [...kids].sort((a, b) => R.get(a).top - R.get(b).top)) {
      const last = col[col.length - 1], r = R.get(k);
      if (!last || (r.top >= R.get(last).bottom - 1 && r.left < R.get(last).right
                    && R.get(last).left < r.right)) col.push(k); }
    const vg = col.slice(1).map((b, n) => [col[n], b, Math.round(R.get(b).top - R.get(col[n]).bottom)])
      .filter(([a, b]) => boxy(a) && boxy(b));
    if (uneven(vg)) gapsOff.push('竖排 ' + fmt(vg));
  }
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
    over, gapsOff: gapsOff.slice(0, 4),
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

const SHOTS = `${S}/shots`
mkdirSync(SHOTS, { recursive: true })
let failed = 0
for (const [who, route, name, min] of PAGES) {
  await send('Page.navigate', { url: 'http://127.0.0.1:8080/' })
  await sleep(700)
  await send('Runtime.evaluate', { expression: `
    localStorage.setItem('yvruc_token', ${JSON.stringify(TOK[who])});
    localStorage.setItem('yvruc_user', ${JSON.stringify(USR[who])});
    localStorage.setItem('yvruc_active_identity', ${JSON.stringify(who)});
    location.assign(${JSON.stringify(route)});` })
  await sleep(2400)
  const r = await send('Runtime.evaluate', { expression: PROBE, returnByValue: true })
  const d = r.timeout
    ? { textLen: 0, errors: ['页面没有响应（主线程卡死？）'], vw: 0, scrollW: 0, over: [], gapsOff: [] }
    : JSON.parse(r.result.result.value)
  const bad = d.scrollW > d.vw + 1 || d.textLen < min || d.errors.length || d.gapsOff.length
  if (bad) {
    failed++
    const shot = await send('Page.captureScreenshot', { format: 'png' })
    if (shot.result) writeFileSync(`${SHOTS}/${W}-${name}.png`, Buffer.from(shot.result.data, 'base64'))
  }
  console.log(`${bad ? '✗' : '✓'} ${name.padEnd(6)} 视口${d.vw} 内容${d.scrollW}` +
    ` | 侧栏${d.aside?'在':'收'} 汉堡${d.burger?'有':'无'}` +
    ` | 卡片${d.cards} 表格${d.tables} 文字${d.textLen}` +
    (d.textLen < min ? `（少于该有的 ${min}）` : '') +
    (d.errors.length ? `\n     JS 错误: ${d.errors[0]}` : '') +
    (d.over.length ? `\n     超宽: ${d.over.join(', ')}` : '') +
    d.gapsOff.map(g => `\n     间距不齐: ${g}`).join(''))
}
ws.close(); proc.kill()
if (failed) { console.log(`${W}×${H}：${failed} 页出 ✗`); process.exitCode = 1 }
