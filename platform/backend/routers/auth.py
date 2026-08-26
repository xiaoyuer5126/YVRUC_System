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
from sqlalchemy.orm import Session as OrmSession

from .. import config
from ..database import db_session
from ..deps import create_session, current_token, current_user
from .. import violations
from ..integrations import sso
from ..locks import clear_failures, failure_count, note_failure
from ..models import Session, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 只数口令错误：一栋楼共用一个出口 IP，按登录次数限流会误伤正常用户
MAX_FAILED_ATTEMPTS = 10
FAIL_WINDOW_SECONDS = 300


def demo_login_enabled() -> bool:
    return bool(config.DEMO_LOGIN_PASSWORD) and sso.mode() == "mock"


@router.get("/sso-mode")
def sso_mode():
    """当前登录方式：真实 SSO 时前端跳转授权页，演示模式时显示口令表单。"""
    if demo_login_enabled():
        return {"mode": "demo", "login_url": ""}
    if sso.mode() == "mock":
        # SSO 未配置且未开演示登录：谁也进不来，如实告诉前端
        return {"mode": "unavailable", "login_url": ""}
    return {"mode": sso.mode(), "login_url": sso.login_url()}


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


@router.post("/sso-callback")
def sso_callback(code: str, db: OrmSession = Depends(db_session)):
    """统一身份认证回调：用授权码换身份，换到谁就给谁开会话。"""
    if sso.mode() == "mock":
        raise HTTPException(409, "统一身份认证尚未接通")
    info = sso.handle_callback(code)
    sid = (info.get("student_id") or "").strip()
    if not sid:
        raise HTTPException(502, "统一身份认证未返回学号")
    user = db.query(User).filter(User.student_id == sid).first()
    if user is None:
        raise HTTPException(404, f"统一身份认证通过，但平台未找到学号 {sid} 的档案"
                                 f"（数智人大同步未覆盖？请联系管理员）")
    return {"token": create_session(db, user), "user": user.full(),
            "mode": sso.mode()}


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


# —— 前端需要的常量 ——

@router.get("/constants")
def constants(user: User = Depends(current_user)):
    from ..integrations import DEFAULTS
    return {
        "activity_types": config.ACTIVITY_TYPES,
        "violation_kinds": violations.KINDS,
        "restriction_months": config.RESTRICTION_MONTHS,
        "colleges": config.COLLEGES,
        "credit": {
            "full": config.CREDIT_FULL,
            "freeze_below": config.CREDIT_FREEZE_BELOW,
            "freeze_days": config.CREDIT_FREEZE_DAYS,
            "late_cancel_penalty": config.LATE_CANCEL_PENALTY,
            "late_cancel_hours": config.LATE_CANCEL_HOURS,
            "confirm_hours": config.PROMOTE_CONFIRM_HOURS,
            "max_abandon": config.MAX_ABANDON_TIMES,
        },
        "integration_labels": {k: v["label"] for k, v in DEFAULTS.items()},
    }
