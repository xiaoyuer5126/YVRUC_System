"""外部系统对接适配层。

设计原则（对应《设计方案》第五节「外部系统对接依赖」）：
  · 每个外部系统一个客户端类，**接口稳定**：Mock 实现和真实 HTTP 实现
    满足同一组方法，业务代码只面向接口；
  · 参数存 integration_configs 表，「集成设置」页可在线切换
    enabled=false（演示/Mock） ↔ true（真实接口），改完即生效、无需重启；
  · 学校信息化部门提供正式接口后，只需在 *_http.py 里补全调用细节。
"""
from __future__ import annotations

from .. import config
from ..database import SessionLocal
from ..models import IntegrationConfig

DEFAULTS: dict[str, dict] = {
    "msgcenter": {
        "label": "消息中心",
        "enabled": False,
        # appid / gateway 默认取环境变量，这里只是兜底、可在「集成设置」页覆盖。
        # secret 不进这张表：它会在 all_configs() 的响应里被原样吐给前端。
        "appid": config.OPENPLATFORM_APPID,
        "gateway": config.OPENPLATFORM_GATEWAY,
        # 通道：1=短信，2=邮件，4=企业微信。**默认只发 4**——短信是按条计费的
        # 真金白银，且学校未同意本平台发短信，代码里任何路径都不得出现 1。
        "channels": "4",
        "mode_hint": "经学校开放平台向学号对应的师生推送消息",
        "capabilities": ["报名成功通知", "候补转正通知", "岗位分配通知",
                          "活动变更警告", "时长核准通知"],
    },
    "sso": {
        "label": "微人大统一身份认证",
        "enabled": False,
        # 只有 OAuth2.0 一条路径：学校给的就是 CAS 上的 OAuth 接口，
        # CAS 原生那条不接（理由见 integrations/sso.py 文件头）。
        # 留空则按 base_url 拼成 {base}/oauth2.0/{authorize,accessToken,user/profiles}
        "base_url": config.CAS_BASE_URL,
        "authorize_url": "",
        "token_url": "",
        "user_info_url": "",
        # client_id/secret 默认借开放平台的 appid/secret（同一套应用注册）。
        # 文档说 OAuth 的 client_id 由「后台 OAuth 配置维护」单独分配，未必是
        # 同一个值——所以留了覆盖口子，对不上时在这里改成学校另给的那套。
        "client_id": config.OPENPLATFORM_APPID,
        "client_secret": "",
        "redirect_uri": config.SSO_REDIRECT_URI,
        "mode_hint": "OAuth2.0，师生登录平台唯一入口；演示期用内置模拟登录",
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
    # appid / client_id 默认来自环境变量：测试环境没有这些变量 → 值为空 →
    # 即便用例把开关打开了也照样回落 Mock，不会误打真实接口。
    "msgcenter": "appid",
    "sso": "client_id",
}
