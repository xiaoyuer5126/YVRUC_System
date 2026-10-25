"""统一身份认证（微人大 SSO）适配。

学校提供的是 **CAS 服务器上的 OAuth2.0 接口**
（`cas.ruc.edu.cn/cas/oauth2.0/*`），2026-09-21 实测跑通；
CAS 原生的 `serviceValidate` 那条路**不接**——两者能拿到的身份字段是同一套，
CAS 只多一个全局登出，代价却是属性要管理员在应用接入里单独开、service 还得
与登记地址一字不差（错一个字符就 INVALID_SERVICE）。留着半条路只会让人以为能选。

    authorize                       → 用户登录后带着 ?code=..&state=.. 回到 redirect_uri
    accessToken（POST 表单）         → access_token
    user/profiles?access_token=..   → 身份信息（scope=all 时可用）

演示模式：`YVRUC_SSO_ENABLED` 没开时不走网络，由 routers/auth.py 的口令登录顶替。
参数都在 .env，启动时由 integrations.check() 核对齐全。
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from .. import config, school_id
from ..locks import issue_sso_state, redeem_sso_state

log = logging.getLogger(__name__)

TIMEOUT = 8

# 相对 CAS 根地址的接口路径；.env 里单独配了某个端点就用那一项
OAUTH_PATHS = {
    "SSO_AUTHORIZE_URL": "oauth2.0/authorize",
    "SSO_TOKEN_URL": "oauth2.0/accessToken",
    "SSO_USER_INFO_URL": "oauth2.0/user/profiles",
}

# profiles 的响应字段名文档没给样例，靠真机登录量出来。2026-09-21 实测
# （学号 2025201717）的响应长这样：
#
#   {"loginName": "2025201717", "name": "…", "gender": "男", "email": null,
#    "phone": "", "categoryName": "学生", "depts": [],
#    "accounts": [{"loginName": "2025201717", "categoryName": "学生",
#                  "childCategoryName": "本科生", "orgCode": "101700",
#                  "orgName": "信息学院", "orgCategoryName": "教学单位",
#                  "enterGrade": "2025", "isMainOrg": true}]}
#
# 手机号与邮箱学校侧是空的，微信、身份证号、校区则根本没有——这几项只能由
# 本人到「个人资料」填，别再指望从响应里找。**不留猜测性候选字段名**：
# 多列一串学校里并不存在的名字，只会让「字段变了」退化成「悄悄挑中另一个
# 值」；真变了，_identity 里的报错会直接说出来。
_ACCOUNT_KEY = "loginName"
_NAME_KEY = "name"

# 人员类别 → 平台的身份类型。文档与实测只出现过这两个取值；认不出就退回
# 按学号位数推（见 school_id.py）。
_TYPE_BY_CATEGORY = {"学生": "student", "教职工": "teacher"}


def mode() -> str:
    """当前登录方式：mock 或 oauth2。只有这一条真实路径，见文件头。"""
    return "oauth2" if config.SSO_ENABLED else "mock"


def _endpoint(name: str) -> str:
    """取端点地址：.env 里单独配了就用它，否则按 CAS_BASE_URL 拼。"""
    explicit = getattr(config, name)
    if explicit:
        return explicit
    return f"{config.CAS_BASE_URL.rstrip('/')}/{OAUTH_PATHS[name]}"


def _client_id() -> str:
    """默认借开放平台的 appid——同一套应用注册。"""
    return config.SSO_CLIENT_ID or config.OPENPLATFORM_APPID


def _client_secret() -> str:
    return config.SSO_CLIENT_SECRET or config.OPENPLATFORM_SECRET


def _redirect_uri() -> str:
    """回调地址，.env 里填**明文**。

    文档明确警告：它必须与应用中心登记的一字不差，且不能是预先 URL 编码过的
    ——下面的 urlencode 自己会编一轮，值若本来就编过就成了二次编码，CAS
    比对不上，直接 invalid_grant。
    """
    return config.SSO_REDIRECT_URI


def login_url(next_url: str = "") -> tuple[str, str]:
    """真实 SSO 的授权跳转地址，返回 (url, nonce)。

    state 现取现用、一次一用，跳转前想去的页面存在它背后（见 locks.py）。
    nonce 由调用方写进浏览器 cookie，回调时比对——只认 state 的话别人能拿
    现成的回调地址替我们发起登录（登录 CSRF）。
    redirect_uri 指向平台根路径，CAS 在后面追加 ?code=..&state=..，
    前端在 router.js 的路由守卫里接住、转给 /sso-callback。
    """
    state, nonce = issue_sso_state(next_url, config.SSO_STATE_SECONDS)
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "scope": "all",
        "state": state,
    })
    return f"{_endpoint('SSO_AUTHORIZE_URL')}?{q}", nonce


def _account(info: dict, sid: str) -> dict:
    """挑出登录所用账号对应的那一条 accounts 记录。

    一个人可能有多重身份（既是学生又是教职工），accounts 就是为这种情况
    准备的。**盲取第 0 条会把学院填成另一个单位的**，所以先按登录名精确
    匹配，退而求其次才用 isMainOrg 标记。
    """
    rows = [a for a in (info.get("accounts") or []) if isinstance(a, dict)]
    for row in rows:
        if _pick(row, _ACCOUNT_KEY) == sid:
            return row
    for row in rows:
        if row.get("isMainOrg"):
            return row
    return rows[0] if rows else {}


def _identity(info: dict) -> dict:
    """把 profiles 的响应翻译成建/更新档案要的字段。

    学校给了就以学校为准——`enterGrade`、`childCategoryName` 比按学号推导
    准得多，留学生那种中间两位对不上对照表的情况也随之消失。学校没给、
    或给了个平台不认识的取值，才退回推导（见 school_id.py）。

    **认不出时绝不硬填**：`stage` 是招募范围的筛选依据，写一个 config.STAGES
    里没有的值进去，人会静默匹配不上任何活动，且事后查不出原因。
    """
    sid = _pick(info, _ACCOUNT_KEY)
    if not sid:
        raise ValueError("统一身份认证未返回可识别的学号/工号")
    acct = _account(info, sid)
    fallback_type, fallback_grade, fallback_stage = school_id.parse(sid)

    stage = _pick(acct, "childCategoryName")
    if stage and stage not in config.STAGES:
        log.warning("统一身份认证返回了未收录的学段：%r（学号 %s），"
                    "已退回按学号推导", stage, sid)
        stage = ""
    category = _pick(acct, "categoryName") or _pick(info, "categoryName")
    return {
        "student_id": sid,
        "name": _pick(info, _NAME_KEY) or sid,
        "user_type": _TYPE_BY_CATEGORY.get(category, fallback_type),
        "grade": _pick(acct, "enterGrade") or fallback_grade,
        "stage": stage or fallback_stage,
        "college": _pick(acct, "orgName"),
        "gender": _pick(info, "gender"),
    }


def handle_callback(code: str, state: str, nonce: str) -> dict:
    """用授权码换身份 → {"student_id", "name", "user_type", "grade",
    "stage", "college", "gender", "next"}。

    state 先核销再换令牌：核销不掉说明这条回调不是从我们这儿发起的
    （伪造、已用过或过期），此时**不该**拿 code 去换令牌。nonce 对不上
    说明发起登录的浏览器不是这台，同样不该换。
    """
    redeemed = redeem_sso_state(state, nonce)
    if redeemed is None or not redeemed[1]:
        raise PermissionError("登录请求已失效，请回到登录页重新点击登录")
    next_url = redeemed[0]

    token = _fetch_token(code)
    info = _fetch_profile(token)
    # 只记字段名不记值：profiles 里装的是姓名、手机号这类个人信息。
    log.info("统一身份认证返回字段：%s", sorted(info))

    ident = _identity(info)
    ident["next"] = next_url
    return ident


def _fetch_token(code: str) -> str:
    raw = _post_form(_endpoint("SSO_TOKEN_URL"), {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "redirect_uri": _redirect_uri(),
    })
    token = _pick(_parse_token(raw), "access_token", "accessToken")
    if not token:
        raise RuntimeError(f"未换到 access_token：{raw[:200]}")
    return token


def _fetch_profile(token: str) -> dict:
    """取用户信息。

    `access_token` 走**查询参数**，不是 `Authorization: Bearer` 头——文档给的
    调用示例就是查询参数。按 OAuth 惯例写成 Bearer 头的话，真机上必然
    取不到用户，而且报错看起来会像是「没登录」。
    """
    url = _endpoint("SSO_USER_INFO_URL")
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(  # noqa: S310
        f"{url}{sep}access_token={urllib.parse.quote(token)}")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8", "replace")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"统一身份认证返回的不是 JSON：{raw[:200]}") from exc
    if not isinstance(body, dict):
        raise RuntimeError(f"统一身份认证返回结构异常：{raw[:200]}")
    return _flatten(body)


def _post_form(url: str, payload: dict) -> str:
    if not url:
        raise RuntimeError("统一身份认证未配置接口地址")
    req = urllib.request.Request(  # noqa: S310
        url, data=urllib.parse.urlencode(payload).encode())
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        return resp.read().decode("utf-8", "replace")


def _parse_token(raw: str) -> dict:
    """换令牌的响应可能是 JSON，也可能是 `key=value&key=value` 的老式串。

    文档没给响应样例，两种都认一下，省得真机上卡在解析这一步干瞪眼。
    """
    try:
        body = json.loads(raw)
        if isinstance(body, dict):
            return body
    except json.JSONDecodeError:
        pass
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}


def _flatten(info: dict) -> dict:
    """把 profiles 的响应压平。

    可能的外层有 data / attributes / user / result，内层还可能是账号数组
    （scope=all 取的是「多账号信息」）。压平后外层字段仍然保留，内层同名字段
    覆盖外层。
    """
    for key in ("data", "attributes", "user", "result"):
        inner = info.get(key)
        if isinstance(inner, dict):
            return {**info, **inner}
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            return {**info, **inner[0]}
    return info


def _pick(info: dict, *names: str) -> str:
    for n in names:
        v = info.get(n)
        if v not in (None, ""):
            return str(v).strip()
    return ""
