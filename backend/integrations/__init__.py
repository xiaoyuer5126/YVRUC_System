"""外部系统对接适配层。

设计原则：
  · 每个外部系统一个客户端类，**接口稳定**：Mock 实现和真实 HTTP 实现
    满足同一组方法，业务代码只面向接口；
  · 开关和参数都在 .env（见 config.py「学校开放平台」一节），改了重启后端生效；
  · 启动时 check() 核对一遍：开关打开却缺参数、或者配了短信通道，直接拒绝启动。
    要是缺参数时悄悄回落演示模式，部署的人会以为已经接通了，直到有人登录不了、
    收不到通知才发现。
"""
from __future__ import annotations

import os

from .. import config

# 开关只认这几种写法；true、yes 之类不认，免得以为打开了其实没开
_FLAG_VALUES = {"", "0", "1"}

# 消息中心能用的通道：2=邮件，4=企业微信。1 是短信，单独报错（见 check）
_CHANNELS = {"2", "4"}


def check() -> None:
    """核对对接配置，有问题就抛 RuntimeError，错误信息写明改哪个变量。"""
    problems: list[str] = []
    for name in ("YVRUC_SSO_ENABLED", "YVRUC_MSGCENTER_ENABLED"):
        raw = os.environ.get(name, "").strip()
        if raw not in _FLAG_VALUES:
            problems.append(f"{name}={raw!r}：只能填 1（真实接口）或留空 / 0（演示模式）")

    codes = [c.strip() for c in config.MSGCENTER_CHANNELS.split(",") if c.strip()]
    if "1" in codes:
        problems.append("YVRUC_MSGCENTER_CHANNELS 含 1（短信）：本平台不使用短信，请去掉")
    unknown = [c for c in codes if c not in _CHANNELS | {"1"}]
    if unknown:
        problems.append(f"YVRUC_MSGCENTER_CHANNELS 含未知通道 {'、'.join(unknown)}："
                        "只能是 2（邮件）、4（企业微信）")

    if config.SSO_ENABLED:
        need = [
            ("YVRUC_SSO_CLIENT_ID（或 YVRUC_OPENPLATFORM_APPID）",
             config.SSO_CLIENT_ID or config.OPENPLATFORM_APPID),
            ("YVRUC_SSO_CLIENT_SECRET（或 YVRUC_OPENPLATFORM_SECRET）",
             config.SSO_CLIENT_SECRET or config.OPENPLATFORM_SECRET),
            ("YVRUC_SSO_REDIRECT_URI", config.SSO_REDIRECT_URI),
            ("YVRUC_CAS_BASE_URL（或把 YVRUC_SSO_AUTHORIZE_URL、YVRUC_SSO_TOKEN_URL、"
             "YVRUC_SSO_USER_INFO_URL 三项都填上）",
             config.CAS_BASE_URL or (config.SSO_AUTHORIZE_URL and config.SSO_TOKEN_URL
                                     and config.SSO_USER_INFO_URL)),
        ]
        missing = [name for name, value in need if not value]
        if missing:
            problems.append("YVRUC_SSO_ENABLED=1，但没有填：" + "、".join(missing))

    if config.MSGCENTER_ENABLED:
        missing = [name for name in ("YVRUC_OPENPLATFORM_APPID", "YVRUC_OPENPLATFORM_SECRET",
                                     "YVRUC_OPENPLATFORM_GATEWAY")
                   if not getattr(config, name.removeprefix("YVRUC_"))]
        if missing:
            problems.append("YVRUC_MSGCENTER_ENABLED=1，但没有填：" + "、".join(missing))

    if problems:
        raise RuntimeError("学校对接配置有误，后端不启动。改好 .env 后执行 "
                           "docker compose up -d：\n  " + "\n  ".join(problems))
