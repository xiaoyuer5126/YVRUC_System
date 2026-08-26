"""外部系统对接适配层。

设计原则（对应《设计方案》第五节「外部系统对接依赖」）：
  · 每个外部系统一个客户端类，**接口稳定**：Mock 实现和真实 HTTP 实现
    满足同一组方法，业务代码只面向接口；
  · 参数存 integration_configs 表，「集成设置」页可在线切换
    enabled=false（演示/Mock） ↔ true（真实接口），改完即生效、无需重启；
  · 学校信息化部门提供正式接口后，只需在 *_http.py 里补全调用细节。
"""
from __future__ import annotations

from ..database import SessionLocal
from ..models import IntegrationConfig

DEFAULTS: dict[str, dict] = {
    "shuzhi_renda": {
        "label": "数智人大",
        "enabled": False,
        "base_url": "",            # 如 https://shuzhi.ruc.edu.cn/openapi
        "app_key": "",
        "app_secret": "",
        "mode_hint": "只读API实时查询 / 每日同步宽表，二选一，由学校确定",
        "capabilities": ["按学号取身份信息", "活动结束回传志愿时长"],
    },
    "wecom_work": {
        "label": "企业微信",
        "enabled": False,
        "corp_id": "",
        "agent_id": "",
        "corp_secret": "",
        "mode_hint": "向学号对应的成员推送应用消息模板",
        "capabilities": ["报名成功通知", "候补转正通知", "岗位分配通知",
                          "活动变更警告", "信用扣分提醒"],
    },
    "checkin_gateway": {
        "label": "签到码系统",
        "enabled": False,
        "base_url": "",
        "app_key": "",
        "app_secret": "",
        "mode_hint": "由对方提供 SDK / RESTful 接口：建任务、核销、状态回调",
        "capabilities": ["生成签到任务与二维码", "核验扫码核销", "签到状态回流"],
    },
    "sso": {
        "label": "微人大统一身份认证",
        "enabled": False,
        "protocol": "oauth2",      # oauth2 / cas
        "authorize_url": "",
        "token_url": "",
        "user_info_url": "",
        "client_id": "",
        "client_secret": "",
        "redirect_uri": "",
        "mode_hint": "OAuth2.0 或 CAS，师生登录平台唯一入口；演示期用内置模拟登录",
        "capabilities": ["师生 SSO 登录"],
    },
}


def get_config(key: str) -> dict:
    """读某个对接配置（带默认值兜底）。"""
    db = SessionLocal()
    try:
        row = db.get(IntegrationConfig, key)
        cfg = dict(DEFAULTS.get(key, {}))
        if row and isinstance(row.value, dict):
            cfg.update(row.value)
        cfg["key"] = key
        return cfg
    finally:
        db.close()


def save_config(key: str, value: dict) -> None:
    db = SessionLocal()
    try:
        row = db.get(IntegrationConfig, key)
        if row is None:
            row = IntegrationConfig(key=key, value=value)
            db.add(row)
        else:
            merged = dict(DEFAULTS.get(key, {}))
            merged.update(row.value or {})
            merged.update(value)
            row.value = merged
        db.commit()
    finally:
        db.close()


def all_configs() -> list[dict]:
    out = []
    for key in DEFAULTS:
        out.append(get_config(key))
    return out


def is_mock(key: str) -> bool:
    """true = 当前走 Mock（演示模式）。

    除了开关未打开，**开了开关但必填参数为空**也回落演示——
    否则请求会打到空地址上，表现为莫名其妙的接口错误。
    """
    cfg = get_config(key)
    if not cfg.get("enabled"):
        return True
    required = REQUIRED_FIELD.get(key)
    if required and not str(cfg.get(required) or "").strip():
        return True
    return False


# 启用真实接口前必须填好的参数
REQUIRED_FIELD = {
    "shuzhi_renda": "base_url",
    "wecom_work": "corp_id",
    "checkin_gateway": "base_url",
    "sso": "authorize_url",
}
