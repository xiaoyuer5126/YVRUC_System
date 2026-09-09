"""检查间距有没有跑出同一套尺度。

区块之间 16px、块内 12px、并排的东西 8px，另有 4px 的微调与 24px 的分隔——
上下外边距和 flex gap 只走这五档。左右外边距不查：那是对齐用的偏移
（缩进、margin-left:auto），跟「两样东西之间留多少白」不是一回事。

写这份检查是因为间距最容易慢慢长回去：随手一个 10px、一个 14px，单看都不
难看，凑到一页上就说不清哪里不对，只觉得乱。

用法：
    python3 scripts/spacecheck.py [目录，默认 web/src]
"""
import pathlib, re, sys

SCALE = {0, 4, 8, 12, 16, 24}
DECL = re.compile(r'(?<![\w-])(margin|margin-top|margin-bottom|gap|row-gap|column-gap)'
                  r'\s*:\s*([^;}"\']+)')


def offending(value: str, prop: str) -> list:
    """返回这条声明里不在尺度上的数值。"""
    parts = value.replace('!important', '').split()
    if prop in ('margin-top', 'margin-bottom', 'row-gap', 'column-gap'):
        picked = parts[:1]
    elif prop == 'gap':
        picked = parts[:2]           # gap: 行 列，两个都是间距
    elif len(parts) in (1, 2):       # margin 简写：只看上下两个方向
        picked = parts[:1]
    elif len(parts) in (3, 4):
        picked = [parts[0], parts[2]]
    else:
        picked = []
    out = []
    for p in picked:
        m = re.fullmatch(r'(-?\d+(?:\.\d+)?)px', p)
        if m and abs(float(m.group(1))) not in SCALE:
            out.append(p)
    return out


bad = 0
root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'web/src')
files = sorted(list(root.rglob('*.vue')) + list(root.rglob('*.css')))
if not files:
    sys.exit(f'{root} 下没有找到 .vue / .css —— 路径给错了？')
for f in files:
    text = f.read_text(encoding='utf-8')
    for m in DECL.finditer(text):
        odd = offending(m.group(2), m.group(1))
        if odd:
            bad += 1
            line = text.count('\n', 0, m.start()) + 1
            print(f'✗ {f}:{line}  {m.group(0).strip()}  ← {"、".join(odd)} 不在 '
                  f'{sorted(SCALE)} 里')
print('全部通过' if not bad else f'{bad} 处间距跑出尺度')
sys.exit(1 if bad else 0)
