"""检查 .vue 里两类不会报错、只会悄悄出事的写法。

1. 用到但从未定义的标识符。Vue 模板对未定义变量既不报错也不告警，只是
   静默渲染成空——「岗位树全空」就是这么来的，构建、类型、测试都发现不了。
2. 把数据当 HTML 渲染（v-html / dangerouslyUseHTMLString: true）。平台里
   几乎每段文字都是别人填的：活动名由发起方填，问卷答案由志愿者填。
   当 HTML 渲染，就等于让填的人在看的人的浏览器里执行脚本，而令牌就存在
   浏览器里。确有必要时在同一行写 `vuecheck: html-ok` 并说明为什么安全。
3. 工具栏（class 含 toolbar）里的小号按钮。按钮尺寸按位置定：工具栏、页头、
   对话框底部是页面级的动作，一律默认尺寸；表格行、卡片项、区块标题旁的
   行内动作才用 small。工具栏里混进小号的，同一排就高矮不齐。
"""
import pathlib, re, sys

BUILTIN = set('''true false null undefined this $event $route $router $emit
$slots $attrs Math Number String Object Array JSON Date console new typeof
in of return item row index window document setTimeout parseInt parseFloat
encodeURIComponent decodeURIComponent Promise Set Map Boolean isNaN'''.split())


def declared(scr: str) -> set:
    names = set()
    names |= set(re.findall(r'(?:const|let|var)\s+([A-Za-z_$][\w$]*)', scr))
    names |= set(re.findall(r'function\s+([A-Za-z_$][\w$]*)', scr))
    # const { a, b } = ... / import { a, b } from
    for block in re.findall(r'(?:const|let|import)\s*\{([^}]*)\}', scr):
        names |= set(re.findall(r'([A-Za-z_$][\w$]*)', block))
    names |= set(re.findall(r'import\s+([A-Za-z_$][\w$]*)\s+from', scr))
    # defineProps({ a: ..., b: ... })
    m = re.search(r'defineProps\(\s*\{(.*?)\}\s*\)', scr, re.S)
    if m:
        names |= set(re.findall(r'([A-Za-z_$][\w$]*)\s*:', m.group(1)))
    return names


def used(tpl: str) -> set:
    exprs = re.findall(r'\{\{(.*?)\}\}', tpl, re.S)
    exprs += re.findall(r'(?:v-if|v-else-if|v-for|v-model|v-show|v-html|'
                        r':[\w.-]+|@[\w.-]+)="([^"]*)"', tpl)
    local = set()
    for m in re.findall(r'v-for="\(?([^)"]*?)\)?\s+in\s', tpl):
        local |= set(re.findall(r'\w+', m))
    for m in re.findall(r'#[\w-]+="\{([^}]*)\}"', tpl):     # 作用域插槽：#default、#footer…
        local |= set(re.findall(r'\w+', m))
    for m in re.findall(r'v-slot:?\w*="\{([^}]*)\}"', tpl):
        local |= set(re.findall(r'\w+', m))
    # 内联箭头函数的形参：@count="n => formCount = n"、format: v => v || '—'
    for a, b in re.findall(r'(?:\(([^)]*)\)|([A-Za-z_$][\w$]*))\s*=>', tpl):
        local |= set(re.findall(r'\w+', a or b))

    out = set()
    for e in exprs:
        e = re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`", '', e)
        if re.match(r'^\s*\(?[\w, ]+\)?\s+in\s+', e):
            e = e.split(' in ', 1)[1]
        for name in re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\b(?!\s*:)', e):
            out.add(name)
    return out - local


def orphan_state(scr: str, tpl: str) -> list:
    """脚本里定义了却没人用的响应式状态。

    典型是「对话框写了 visible 标志，模板里却没有对应的 el-dialog」——
    点按钮只是把一个没人渲染的 visible 置成 true，界面毫无反应，
    构建和类型检查都不会吭声。
    """
    out = []
    for line, name in re.findall(
            r'(const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:ref|reactive|computed)\([^\n]*)', scr):
        if 'vuecheck: script-only' in line:
            continue          # 确实只在脚本里用的，声明处标一下即可
        if re.search(rf'(?<![.\w]){re.escape(name)}\b', tpl):
            continue
        out.append(name)
    return out


def html_sinks(src: str) -> list:
    """把数据当 HTML 渲染的地方。dangerouslyUseHTMLString: false 是明示关掉的，
    不算；写成别的值（变量、true）都得说明理由。"""
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        if 'vuecheck: html-ok' in line:
            continue
        if re.search(r'\bv-html\b', line):
            out.append(f'{i}: v-html')
        m = re.search(r'dangerouslyUseHTMLString\s*:\s*(\w+)', line)
        if m and m.group(1) != 'false':
            out.append(f'{i}: dangerouslyUseHTMLString: {m.group(1)}')
    return out


TAG = re.compile(r'<(/?)([A-Za-z][\w-]*)((?:[^>"\']|"[^"]*"|\'[^\']*\')*?)(/?)>')
VOID = {'input', 'img', 'br', 'hr', 'col', 'meta', 'link'}


def toolbar_small_buttons(src: str) -> list:
    """工具栏里写了 size="small" 的按钮（行号）。"""
    tpl = src.split('<script', 1)[0]
    out, stack = [], []
    for m in TAG.finditer(tpl):
        closing, tag, attrs, selfclose = m.groups()
        if closing:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == tag:
                    del stack[i:]
                    break
            continue
        if tag == 'el-button' and re.search(r'\bsize="small"', attrs) \
                and any(is_bar for _, is_bar in stack):
            out.append(f'{tpl[:m.start()].count(chr(10)) + 1}')
        if not selfclose and tag not in VOID:
            stack.append((tag, bool(re.search(r'\bclass="[^"]*\btoolbar\b', attrs))))
    return out


bad = 0
files = sorted(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '.').rglob('*.vue'))
if not files:
    sys.exit('没有找到任何 .vue —— 路径给错了？（这正是上次误报「全部通过」的原因）')
for f in files:
    src = f.read_text(encoding='utf-8')
    tpl_m = re.search(r'<template>(.*)</template>\s*<script', src, re.S)
    scr_m = re.search(r'<script setup>(.*?)</script>', src, re.S)
    if not (tpl_m and scr_m):
        continue
    scr = scr_m.group(1)
    names = declared(scr)
    tpl = tpl_m.group(1)
    missing = sorted(n for n in used(tpl)
                     if n not in BUILTIN and n not in names)
    orphans = orphan_state(scr, tpl)
    # 脚本里用了却没定义的，同样是漏改（rosterOptions 引用 groups 就是这样）
    scr_undef = sorted({n for n in re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\.value\b', scr)
                        if n not in names})
    sinks = html_sinks(src)
    small_in_bar = toolbar_small_buttons(src)
    if missing or scr_undef or orphans or sinks or small_in_bar:
        bad += 1
        print(f'✗ {f}')
        if sinks:
            print(f'    把数据当 HTML 渲染: {sinks}')
        if missing:
            print(f'    模板用到但未定义: {missing}')
        if scr_undef:
            print(f'    脚本用到但未定义: {scr_undef}')
        if orphans:
            print(f'    定义了却没人用（对话框漏写模板？）: {orphans}')
        if small_in_bar:
            print(f'    工具栏里的小号按钮（工具栏一律默认尺寸）: 第 {"、".join(small_in_bar)} 行')
print('全部通过' if not bad else f'{bad} 个文件有问题')
sys.exit(1 if bad else 0)
