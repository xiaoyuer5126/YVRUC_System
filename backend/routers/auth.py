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
    return {"token": create_session(db, user), "user": user.full(), "mode": "demo"}


@router.get("/demo-accounts")
def demo_accounts(db: OrmSession = Depends(db_session)):
    """演示登录页的快捷账号列表。不含口令，拿到也登不进去。"""
    if not demo_login_enabled():
        return []
    all_users = db.query(User).order_by(User.student_id).all()
    staff = [u for u in all_users if u.is_manager_identity()][:4]
    students = [u for u in all_users
                if u.user_type == "student" and not u.is_manager_identity()][:8]

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
        user = _provision(db, sid, info.get("name") or sid)
    return {"token": create_session(db, user), "user": user.full(),
            "mode": sso.mode(), "next": info.get("next") or ""}


def _provision(db: OrmSession, sid: str, name: str) -> User:
    """给第一次登录的师生建本地档案。

    平台不对志愿者设准入名单，师生第一次登录就该能用；要是要求先由管理员
    录入，SSO 接通了也没人进得来。年级与学段从学号推导（见 school_id.py），
    手机号、微信、学院由本人到「个人资料」补全——缺手机号/微信本来就会拦住
    报名并弹完善表单，这条路径早就有了。

    **不预置任何身份**：学生给 student，教师留空。权限必须由管理员在「角色」
    页显式授予，不能因为「他是老师」就自动拿到管理入口。
    """
    user_type, grade, stage = school_id.parse(sid)
    user = User(id=uid("u"), student_id=sid, name=name,
                user_type=user_type, grade=grade, stage=stage,
                roles=["student"] if user_type == "student" else [])
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
def me(user: User = Depends(current_user)):
    return {"user": user.full()}


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
    if not path.startswith("/api/"):
        raise HTTPException(400, "下载地址不合法")
    return {"ticket": issue_ticket(user.id, path.split("?")[0],
                                   config.DOWNLOAD_TICKET_SECONDS)}


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
