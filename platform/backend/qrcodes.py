"""二维码生成（签到码用）。segno 纯 Python 实现，输出 SVG 供前端直接内嵌。"""
from __future__ import annotations

import io

import segno


def qr_svg(data: str, scale: int = 6) -> str:
    """把任意字符串（通常是签到落地页 URL）渲染成 SVG 二维码。"""
    qr = segno.make(data, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=scale, border=2,
            dark="#1f2329", light="#ffffff")
    return buf.getvalue().decode("utf-8")
