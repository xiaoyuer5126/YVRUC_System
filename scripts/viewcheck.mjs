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
//
// 「按钮间距」「按钮高矮」不看 DOM 怎么嵌套，只看按钮落在屏幕上的位置：页面内容区
// 里同一排（垂直中心差不到 6px）、水平紧挨着（中间没有文字、别的按钮或输入控件）
// 的两个 el-button，间距必须是 8px（±1px；40px 以上算 margin-left:auto 有意推开，
// 不比），两个都不是文字按钮时高度差不能到 2px。表格、下拉面板、日期面板、分页条、
// 按钮组（el-button-group 本来就贴在一起）不比。按钮被 tooltip、popconfirm 包了
// 几层，只比同一个父元素的「横排」看不见它，这一条看得见。
//
// 「顶边」：.page 和当前页签的内容区里，第一个可见的块（顺着第一个子元素往里，
// 穿过没有上内边距、上边框的普通块）与容器内容区上沿差 1px 以上就报，写明哪个容器、
// 差多少——标题、提示条自带的上外边距会在容器内边距之外再顶出一截。
// 「居中」：表格里 align="center" 的单元格（td.is-center .cell）放着按钮时，按钮、
// 标签和文字的可见边界到单元格左右两边的留白差 2px 以上就报。
//
// 桌面视口按鼠标设备模拟，手机视口按触屏模拟：样式表里电脑端和触屏的规则分别挂在
// (hover: hover) and (pointer: fine) 和 (hover: none) and (pointer: coarse) 下。
// 无头浏览器没有鼠标，默认两个都不成立（pointer: none），两遍测到的都不是用户
// 看到的样子。桌面那遍用 --blink-settings 把指针设成鼠标（fine + hover），并按
// mobile:false、不模拟触屏；手机那遍打开触屏模拟，指针成了 coarse。每遍开头打印
// (hover: hover) and (pointer: fine) 是否成立，桌面那遍不成立直接判失败。
import { spawn } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
const CHROME = process.env.CHROME
  || '/home/xiaoyuer5126/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome'
const S = process.argv[2], W = +process.argv[3], H = +process.argv[4]
const DESKTOP = W > 860
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
  ['admin', `/manage/${ACT}?tab=overview`, '概览', 200],
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
// 指针、悬停的取值是位标志：鼠标 = fine(4)，能悬停 = hover(2)
const MOUSE = '--blink-settings=primaryPointerType=4,availablePointerTypes=4,'
  + 'primaryHoverType=2,availableHoverTypes=2'
const proc = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, ...(DESKTOP ? [MOUSE] : []),
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
await send('Emulation.setDeviceMetricsOverride',{width:W,height:H,deviceScaleFactor:2,mobile:!DESKTOP})
await send('Emulation.setTouchEmulationEnabled', DESKTOP ? { enabled: false } : { enabled: true, maxTouchPoints: 5 })

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
  // 按钮间距与高矮：按屏幕位置找同一排里水平紧挨着的两个按钮，不管 DOM 嵌套
  const root = document.querySelector('.app-main') || document.body;
  const SKIP = '.el-table, .el-select-dropdown, .el-picker-panel, .el-pagination, .el-popper, .el-overlay';
  const seen = el => { const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const btns = [...root.querySelectorAll('.el-button')].filter(b => seen(b) && !b.closest(SKIP))
    .map(b => ({ el: b, r: b.getBoundingClientRect(), link: b.matches('.is-link, .is-text') }));
  // 挡在两个按钮之间的，说明这两个不是紧挨着的一对：文字、输入类控件、图标图片
  const blocks = [...root.querySelectorAll('.el-input, .el-select, .el-date-editor, .el-input-number, '
      + '.el-switch, .el-checkbox, .el-radio-group, .el-slider, .el-avatar, svg, img, canvas')]
    .filter(el => !el.closest('.el-button') && seen(el)).map(el => el.getBoundingClientRect());
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n; (n = tw.nextNode());) {
    if (!n.textContent.trim() || n.parentElement.closest('.el-button')) continue;
    const range = document.createRange(); range.selectNodeContents(n);
    for (const r of range.getClientRects()) if (r.width > 0) blocks.push(r);
  }
  const between = (a, b) => blocks.some(o => o.right > a.r.right + 0.5 && o.left < b.r.left - 0.5
    && o.bottom > Math.min(a.r.top, b.r.top) && o.top < Math.max(a.r.bottom, b.r.bottom));
  const btnOff = [];
  for (const a of btns) {
    const cy = a.r.top + a.r.height / 2;
    const b = btns.filter(x => x !== a && x.r.left >= a.r.right - 1
        && Math.abs(x.r.top + x.r.height / 2 - cy) < 6)
      .sort((x, y) => x.r.left - y.r.left)[0];
    if (!b || between(a, b)) continue;
    const grp = a.el.closest('.el-button-group');
    if (grp && grp === b.el.closest('.el-button-group')) continue;
    const g = b.r.left - a.r.right;
    if (g < 40 && Math.abs(g - 8) > 1)
      btnOff.push('按钮间距 「' + label(a.el) + '」‹' + Math.round(g) + 'px›「' + label(b.el) + '」，应为 8px');
    if (!a.link && !b.link && Math.abs(a.r.height - b.r.height) >= 2)
      btnOff.push('按钮高矮 「' + label(a.el) + '」' + Math.round(a.r.height) + 'px ／「'
        + label(b.el) + '」' + Math.round(b.r.height) + 'px');
  }
  // 顶边：.page 和当前页签的内容区里，第一个可见的块要贴着容器内容区的上沿
  // （容器 top + 上边框 + 上内边距），差 1px 以上报出来。外边距会穿过没有上内边距、
  // 上边框的块继续往上叠：组件根节点是个空壳 div、里面第一个是提示条，提示条的
  // 12px 照样把整块往下顶。所以顺着「第一个可见子元素」往里走，只穿过普通块和
  // 竖排的 flex，走到最里面那一层再量；遇到前面先有文字、或下一层是行内元素就停。
  // 页签量的是 .el-tabs__content：第一个块的外边距穿过 .el-tab-pane 时，挪下去的
  // 是 pane 自己，拿 pane 当基准就看不出来
  const px = (el, p) => parseFloat(getComputedStyle(el)[p]) || 0;
  const BLOCKY = /^(block|flex|grid|table|flow-root|list-item)$/;
  const leadEl = el => { for (const n of el.childNodes) {
    if (n.nodeType === 3 && n.textContent.trim()) return null;
    if (n.nodeType === 1 && vis(n)) return n; } return null; };
  const passThrough = el => { const cs = getComputedStyle(el);
    return (/^(block|flow-root|list-item)$/.test(cs.display)
            || (cs.display === 'flex' && cs.flexDirection.startsWith('column')))
      && px(el, 'paddingTop') === 0 && px(el, 'borderTopWidth') === 0; };
  const topOff = [];
  const edge = (name, box) => {
    let el = box, k;
    while ((k = leadEl(el)) && BLOCKY.test(getComputedStyle(k).display)) { el = k; if (!passThrough(el)) break; }
    if (el === box) return;
    const d = Math.round(el.getBoundingClientRect().top
      - (box.getBoundingClientRect().top + px(box, 'borderTopWidth') + px(box, 'paddingTop')));
    if (Math.abs(d) > 1) topOff.push('顶边 ' + name + ' 第一个元素「' + label(el) + '」离内容区上沿 ' + d + 'px');
  };
  for (const pg of document.querySelectorAll('.page')) if (seen(pg)) edge('.page', pg);
  for (const tc of document.querySelectorAll('.el-tabs__content')) {
    if (!seen(tc)) continue;
    const tab = tc.closest('.el-tabs').querySelector('.el-tabs__item.is-active');
    edge('页签「' + (tab ? label(tab) : '?') + '」', tc);
  }
  // 居中：表格里 align="center" 的单元格放着按钮时，按钮（连同旁边的标签、文字）
  // 落在屏幕上的左右边界，到单元格内容区两边的留白要一样，差 2px 以上报出来。
  // 单元格里套了一层撑满整格的 flex 容器时，text-align:center 管不到它的子项，
  // 按钮就贴在左边
  const centerOff = [];
  for (const cell of root.querySelectorAll('.el-table td.is-center .cell')) {
    if (!seen(cell) || !cell.querySelector('.el-button')) continue;
    const rs = [...cell.querySelectorAll('.el-button, .el-tag, .el-input, .el-select, svg, img')]
      .filter(seen).map(el => el.getBoundingClientRect());
    const tw2 = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
    for (let n; (n = tw2.nextNode());) {
      if (!n.textContent.trim()) continue;
      const range = document.createRange(); range.selectNodeContents(n);
      for (const r of range.getClientRects()) if (r.width > 0) rs.push(r);
    }
    if (!rs.length) continue;
    const c = cell.getBoundingClientRect();
    const dl = Math.min(...rs.map(r => r.left)) - (c.left + px(cell, 'borderLeftWidth') + px(cell, 'paddingLeft'));
    const dr = (c.right - px(cell, 'borderRightWidth') - px(cell, 'paddingRight')) - Math.max(...rs.map(r => r.right));
    if (Math.abs(dl - dr) > 2)
      centerOff.push('居中 「' + label(cell) + '」左留白 ' + Math.round(dl) + 'px ／右留白 ' + Math.round(dr) + 'px');
  }
  // 非文字按钮的高度，白色（默认类型）和带颜色的分开记，汇总在这一遍输出的末尾
  const heights = btns.filter(b => !b.link).map(b => [
    /el-button--(primary|success|warning|danger|info)/.test(b.el.className) ? '彩色' : '白色',
    Math.round(b.r.height)]);
  return JSON.stringify({
    fine: matchMedia('(hover: hover) and (pointer: fine)').matches,
    btnOff: [...new Set(btnOff)].slice(0, 6), heights,
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
    topOff: [...new Set(topOff)].slice(0, 4), centerOff: [...new Set(centerOff)].slice(0, 4),
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
let failed = 0, fine
const heights = {}
for (const [who, route, name, min] of PAGES) {
  await send('Page.navigate', { url: 'http://127.0.0.1:8080/' })
  await sleep(700)
  await send('Runtime.evaluate', { expression: `
    localStorage.setItem('yvruc_token', ${JSON.stringify(TOK[who])});
    localStorage.setItem('yvruc_user', ${JSON.stringify(USR[who])});
    sessionStorage.setItem('yvruc_active_identity', ${JSON.stringify(who)});
    location.assign(${JSON.stringify(route)});` })
  await sleep(2400)
  const r = await send('Runtime.evaluate', { expression: PROBE, returnByValue: true })
  const d = r.timeout
    ? { textLen: 0, errors: ['页面没有响应（主线程卡死？）'], vw: 0, scrollW: 0, over: [], gapsOff: [],
        btnOff: [], heights: [], topOff: [], centerOff: [] }
    : JSON.parse(r.result.result.value)
  if (fine === undefined && !r.timeout) {
    fine = d.fine
    console.log(`${W}×${H} ${DESKTOP ? '鼠标' : '触屏'}模拟：(hover: hover) and (pointer: fine) = ${fine}`)
  }
  for (const [kind, h] of d.heights) heights[`${kind} ${h}px`] = (heights[`${kind} ${h}px`] || 0) + 1
  const bad = d.scrollW > d.vw + 1 || d.textLen < min || d.errors.length || d.gapsOff.length
    || d.btnOff.length || d.topOff.length || d.centerOff.length
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
    d.gapsOff.map(g => `\n     间距不齐: ${g}`).join('') +
    [...d.btnOff, ...d.topOff, ...d.centerOff].map(g => `\n     ${g}`).join(''))
}
ws.close(); proc.kill()
console.log('非文字按钮高度（种类 高度×个数）：' + Object.entries(heights)
  .sort().map(([k, n]) => `${k}×${n}`).join('，'))
if (DESKTOP && fine !== true) {
  console.log('桌面视口下 (hover: hover) and (pointer: fine) 不成立：电脑端样式没有被测到')
  process.exitCode = 1
}
if (failed) { console.log(`${W}×${H}：${failed} 页出 ✗`); process.exitCode = 1 }
