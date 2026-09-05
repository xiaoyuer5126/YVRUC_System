"""二维码生成。segno 纯 Python 实现，输出 SVG 供前端直接内嵌。"""
from __future__ import annotations

import io

import segno
from fastapi import Request, Response


def qr_svg(data: str, scale: int = 6) -> str:
    """把任意字符串渲染成 SVG 二维码。"""
    qr = segno.make(data, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=scale, border=2,
            dark="#1f2329", light="#ffffff")
    return buf.getvalue().decode("utf-8")


def page_qr(request: Request, path: str, base: str = "") -> Response:
    """站内页面的二维码。内容一定是**绝对网址**。

    编相对路径是不行的：手机相机扫到「/#/checkin/AB12CD」不会跳转，只把这
    行字显示出来——码是好码，但它编的不是一个网址。签到码与服装码此前就是
    这样，扫了进不了网站。

    base（形如 https://host/）由前端传，那是管理者浏览器此刻的地址，也正是
    要让志愿者打开的地址，最可靠。没传才按请求头兜底——反代后面推不准：
    cloudflared 到 nginx 之间是明文 http，nginx 又把 X-Forwarded-Proto
    覆写成 $scheme，照着推会得到一个 http:// 的错网址。
    """
    root = (base or str(request.base_url)).rstrip("/")
    return Response(content=qr_svg(f"{root}/#{path}"),
                    media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})
