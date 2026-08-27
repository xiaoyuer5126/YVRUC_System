"""检查 .vue 里用到但从未定义的标识符。

Vue 模板对未定义变量既不报错也不告警，只是静默渲染成空——
「岗位树全空」就是这么来的，构建、类型、测试全都发现不了。
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
    for m in re.findall(r'#default="\{([^}]*)\}"', tpl):
        local |= set(re.findall(r'\w+', m))
    for m in re.findall(r'v-slot:?\w*="\{([^}]*)\}"', tpl):
        local |= set(re.findall(r'\w+', m))

    out = set()
    for e in exprs:
        e = re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`", '', e)
        if re.match(r'^\s*\(?[\w, ]+\)?\s+in\s+', e):
            e = e.split(' in ', 1)[1]
        for name in re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\b(?!\s*:)', e):
            out.add(name)
    return out - local


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
    missing = sorted(n for n in used(tpl_m.group(1))
                     if n not in BUILTIN and n not in names)
    # 脚本里用了却没定义的，同样是漏改（rosterOptions 引用 groups 就是这样）
    scr_undef = sorted({n for n in re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\.value\b', scr)
                        if n not in names})
    if missing or scr_undef:
        bad += 1
        print(f'✗ {f}')
        if missing:
            print(f'    模板用到但未定义: {missing}')
        if scr_undef:
            print(f'    脚本用到但未定义: {scr_undef}')
print('全部通过' if not bad else f'{bad} 个文件有问题')
sys.exit(1 if bad else 0)
