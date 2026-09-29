"""认证与基础元数据接口。

登录唯一入口是学校统一身份认证（微人大 SSO），由 integrations/sso.py 适配。

SSO 接通之前，开发小组需要一个能进系统的口子：`/api/auth/demo-login`
选学号登录，但**必须同时满足**两个条件才存在——SSO 尚未接通，且部署时
配置了 RUCVA_DEMO_PASSWORD 共享口令。任一不满足，这个接口连路由都不注册。
学校 SSO 上线后，删掉本文件里 demo 相关的三个接口即可，其余代码不受影响。
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from .. import config, school_id
from ..database import db_session
from ..deps import create_session, current_token, current_user
from ..integrations import sso
from ..liaison import session_user
from ..locks import (clear_failures, failure_count, issue_ticket,
                     note_failure)
from ..models import Session, User, ViolationRecord, uid

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 只数口令错误：一栋楼共用一个出口 IP，按登录次数限流会误伤正常用户
MAX_FAILED_ATTEMPTS = 10
FAIL_WINDOW_SECONDS = 300


def demo_login_enabled() -> bool:
    return bool(config.DEMO_LOGIN_PASSWORD) and sso.mode() == "mock"


@router.get("/sso-mode")
def sso_mode():
    """当前登录方式：真实 SSO 时前端显示跳转按钮，演示模式时显示口令表单。

    只回**方式**与**是否配好**，不回跳转地址——那个要点击时才取，见下。
    """
    if demo_login_enabled():
        return {"mode": "demo", "ready": False}
    if sso.mode() == "mock":
        # SSO 未配置且未开演示登录：谁也进不来，如实告诉前端
        return {"mode": "unavailable", "ready": False}
    return {"mode": sso.mode(), "ready": sso.configured()}


@router.get("/sso-login-url")
def sso_login_url(next: str = ""):
    """取授权跳转地址。

    在**点击时**才生成：state 一次一用且有有效期，页面一打开就生成的话，
    用户看一会儿再点就已经过期了。顺带把「登录后回哪去」放进 state——
    从二维码落地页被弹到登录页的人（CheckinLanding.vue），登录完该回到
    原来那个页面，而不是活动广场。
    """
    if sso.mode() == "mock":
        raise HTTPException(409, "统一身份认证尚未接通")
    url = sso.login_url(next)
    if not url:
        raise HTTPException(503, "统一身份认证未配置，请联系管理员")
    return {"login_url": url}


class DemoLoginBody(BaseModel):
    student_id: str
    password: str


@router.post("/demo-login")
def demo_login(body: DemoLoginBody, request: Request,
               db: OrmSession = Depends(db_session)):
    if not demo_login_enabled():
        raise HTTPException(404, "演示登录未启用，请通过学校统一身份认证登录")

    source = f"login:{request.client.host if request.client else 'unknown'}"
    if failure_count(source) >= MAX_FAILED_ATTEMPTS:
        raise HTTPException(429, "口令错误次数过多，请 5 分钟后再试")

    # compare_digest：口令比对不能因为长度/前缀不同而提前返回
    if not secrets.compare_digest(body.password, config.DEMO_LOGIN_PASSWORD):
        note_failure(source, FAIL_WINDOW_SECONDS)
        raise HTTPException(401, "演示口令不正确")

    sid = body.student_id.strip()
    user = db.query(User).filter(User.student_id == sid).first()
    if user is None:
        raise HTTPException(404, f"平台没有学号 {sid} 的档案")
    clear_failures(source)
    return {"token": create_session(db, user), "user": session_user(db, user),
            "mode": "demo"}


@router.get("/demo-accounts")
def demo_accounts(db: OrmSession = Depends(db_session)):
    """演示登录页的快捷账号列表。不含口令，拿到也登不进去。"""
    if not demo_login_enabled():
        return []
    all_users = db.query(User).order_by(User.student_id).all()
    staff = [u for u in all_users if u.is_manager_identity(real=True)][:4]
    students = [u for u in all_users
                if u.user_type == "student"
                and not u.is_manager_identity(real=True)][:8]

    def label(u: User) -> str:
        kind = "老师" if u.user_type == "teacher" else "学生"
        return f"{u.name}｜{u.student_id}｜{kind}·{u.active_role_label()}"

    return [{"label": label(u), "student_id": u.student_id,
             "roles": u.roles or [], "user_type": u.user_type}
            for u in [*staff, *students]]


class SsoCallbackBody(BaseModel):
    # code 与 state 走请求体而不是查询串：查询串会原样进 nginx 访问日志和
    # Referer，而授权码就是一张能换身份的凭证。
    code: str
    state: str


@router.post("/sso-callback")
def sso_callback(body: SsoCallbackBody,
                 db: OrmSession = Depends(db_session)):
    """统一身份认证回调：用授权码换身份，换到谁就给谁开会话。"""
    if sso.mode() == "mock":
        raise HTTPException(409, "统一身份认证尚未接通")
    try:
        info = sso.handle_callback(body.code, body.state)
    except PermissionError as exc:
        # state 核销不掉：伪造、已用过或过期，这条回调不该被信任
        raise HTTPException(400, str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(502, f"统一身份认证校验失败：{exc}") from exc

    sid = (info.get("student_id") or "").strip()
    if not sid:
        raise HTTPException(502, "统一身份认证未返回学号")

    user = db.query(User).filter(User.student_id == sid).first()
    if user is None:
        user = _provision(db, info)
    else:
        _sync_from_school(db, user, info)
    return {"token": create_session(db, user), "user": session_user(db, user),
            "mode": sso.mode(), "next": info.get("next") or ""}


# 学校是这几项的权威来源：升学年、转专业、改名都会在下次登录时同步过来。
# 手机号、微信、身份证号、校区**不在此列**——学校侧没有这些数据，是本人
# 自己填的，这里覆盖一下就等于把人填好的东西清空了。
_SCHOOL_FIELDS = ("name", "user_type", "grade", "stage", "college", "gender")


def _sync_from_school(db: OrmSession, user: User, ident: dict) -> None:
    """每次登录都用学校的说法刷新档案。

    只覆盖学校**确实给了值**的字段：返回空值意味着学校那边也没有这项数据，
    不该拿它去清掉本地已有的值。
    """
    changed = [f for f in _SCHOOL_FIELDS
               if ident.get(f) and getattr(user, f) != ident[f]]
    if not changed:
        return
    for f in changed:
        setattr(user, f, ident[f])
    db.commit()


def _provision(db: OrmSession, ident: dict) -> User:
    """给第一次登录的师生建本地档案。

    平台不对志愿者设准入名单，师生第一次登录就该能用；要是要求先由管理员
    录入，SSO 接通了也没人进得来。年级、学段、学院、性别学校在登录时一并
    给出（见 integrations/sso.py 的 `_identity`），手机号、微信、身份证号、
    校区学校没有，由本人到「个人资料」补全——缺任一项都会拦住报名并弹完善
    表单，这条路径早就有了。

    按身份类型预置角色：学生给 student，老师给 initiator——老师自动是活动
    发起方，能直接发起活动；管理类权限仍由管理员在「角色」页显式授予。
    老师不参与志愿报名，也就不需要补充手机号等个人信息。
    """
    sid = ident["student_id"]
    # _identity 通常已经把学校没给的部分按学号补好了（见 integrations/sso.py）。
    # 这里再兜一次，是为了让残缺的 ident 不至于静默建出一个没有年级学段、
    # 连身份类型都错了的档案——那种错要等到筛选人时才看得出来。
    fallback_type, fallback_grade, fallback_stage = school_id.parse(sid)
    user_type = ident.get("user_type") or fallback_type
    user = User(id=uid("u"), student_id=sid,
                name=ident.get("name") or sid, user_type=user_type,
                grade=ident.get("grade") or fallback_grade,
                stage=ident.get("stage") or fallback_stage,
                college=ident.get("college") or "",
                gender=ident.get("gender") or "",
                roles=["student"] if user_type == "student" else ["initiator"])
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # 同一个人同时点了两下登录：student_id 的唯一索引挡下第二条，
        # 改用已经建好的那条，别把第二次登录变成 500
        db.rollback()
        existing = db.query(User).filter(User.student_id == sid).first()
        if existing is None:
            raise
        return existing
    return user


@router.post("/logout")
def logout(token: str = Depends(current_token),
           db: OrmSession = Depends(db_session),
           user: User = Depends(current_user)):
    """只吊销当前这一个会话，不影响该用户在别的设备上的登录。"""
    row = db.get(Session, token)
    if row is not None:
        db.delete(row)
        db.commit()
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user),
       db: OrmSession = Depends(db_session)):
    return {"user": session_user(db, user)}


class TicketBody(BaseModel):
    path: str


@router.post("/download-ticket")
def download_ticket(body: TicketBody, user: User = Depends(current_user)):
    """换一张一次性下载票。

    导出与学习材料是 <a> 直链，发不出 Authorization 头。票绑定这一个网址、
    一分钟有效、用一次即作废，所以就算随地址落进日志也换不出任何东西。
    权限仍由目标接口自己把关，这张票只证明「是谁」。
    """
    path = body.path.strip()
    # 「|」是票据内容的分隔符（见 locks.issue_ticket），合法网址里不会有
    if not path.startswith("/api/") or "|" in path:
        raise HTTPException(400, "下载地址不合法")
    # 票记下换票时的身份视角：下载时按同一个身份判权（见 deps.current_user_download）
    return {"ticket": issue_ticket(user.id, path.split("?")[0],
                                   config.DOWNLOAD_TICKET_SECONDS,
                                   user.view() or "")}


# —— 前端需要的常量 ——

@router.get("/constants")
def constants(user: User = Depends(current_user)):
    from ..integrations import DEFAULTS
    return {
        "recruit_modes": config.RECRUIT_MODES,
        "violation_kinds": ViolationRecord.KINDS,
        "colleges": config.COLLEGES,
        "campuses": config.CAMPUSES,
        "stages": config.STAGES,
        "rules": {
            "late_cancel_hours": config.LATE_CANCEL_HOURS,
            "confirm_hours": config.PROMOTE_CONFIRM_HOURS,
        },
        "integration_labels": {k: v["label"] for k, v in DEFAULTS.items()},
    }
