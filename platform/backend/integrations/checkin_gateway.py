"""签到码系统适配。

《设计方案》阶段四：平台调用学校现有"签到码生成与核验"接口，
扫码数据自动回流标记"已到岗"。

本适配层把「任务」抽象成 CheckinTask（models.py）：
  · 演示模式：token 由平台自己生成（6位大写字母数字），核验在本平台完成；
  · 对接模式：create_task() 调对方接口拿正式任务号存入 token 字段，
    verify() 回调对方核验。表结构不变，切换只动这个文件。
"""
from __future__ import annotations

import secrets
import string

from . import is_mock

ALPHABET = string.ascii_uppercase + string.digits


def create_task(mode: str, group_key: str, station_key: str = "") -> str:
    """生成签到任务的 token。

    mode: 'group' 统一签到 / 'position' 分岗位签到。
    """
    if is_mock("checkin_gateway"):
        return "".join(secrets.choice(ALPHABET) for _ in range(6))
    # 真实模式：调学校签到系统创建任务并返回其任务号（占位）
    raise NotImplementedError("待学校提供签到系统接口后在此实现 create_task")


def verify_scan(token: str) -> bool:
    """核验扫码。演示模式恒真（有效性判断在路由层做：时间窗/岗位时段）。"""
    if is_mock("checkin_gateway"):
        return True
    raise NotImplementedError("待学校提供签到系统接口后在此实现 verify_scan")
