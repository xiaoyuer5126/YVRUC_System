"""认证与基础元数据接口。

登录走 SSO 适配层：演示模式 = 模拟"统一身份认证已通过"（输入/点选学号即登录）；
学校接入微人大 OAuth2/CAS 后，由 /api/auth/sso-url + 回调接管，前端无需大改。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from .. import config
from ..database import db_session
from ..deps import create_session, current_user
from ..integrations import all_configs, get_config, sso
from ..models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    student_id: str
    name: str = ""          # 首次登录且本地无此学号时用于建档（演示模式）


@router.get("/sso-mode")
def sso_mode():
    """当前登录方式与演示账号提示。"""
    cfg = get_config("sso")
    return {"mode": sso.mode(), "login_url": ("" if cfg.get("enabled") else "")}


@router.post("/login")
def login(body: LoginBody, db: OrmSession = Depends(db_session)):
    # 真实模式应先经 handle_callback(code)；演示模式直接视为认证已通过。
    sid = body.student_id.strip()
    user = db.query(User).filter(User.student_id == sid).first()
    if user is None:
        raise HTTPException(404, f"统一身份认证通过，但平台未找到学号 {sid} 的档案"
                                 f"（数智人大同步未覆盖？请联系管理员）")
    token = create_session(db, user)
    return {"token": token, "user": user.full(),
            "mode": sso.mode()}


@router.get("/demo-accounts")
def demo_accounts(db: OrmSession = Depends(db_session)):
    """演示模式登录页的快捷账号（仅模拟 SSO 时提供）。"""
    if sso.mode() != "mock":
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


@router.post("/logout")
def logout(db: OrmSession = Depends(db_session),
           user: User = Depends(current_user)):
    from ..models import Session
    row = (db.query(Session)
           .filter(Session.user_id == user.id).all())
    for r in row:
        db.delete(r)
    db.commit()
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"user": user.full()}


# —— 前端需要的常量 —— 

@router.get("/constants")
def constants():
    from ..integrations import DEFAULTS
    return {
        "activity_types": config.ACTIVITY_TYPES,
        "violation_types": config.VIOLATION_TYPES,
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


@router.get("/integrations-public")
def integrations_public():
    """首页脚注展示对接状态（只回 label+enabled，不回密钥）。"""
    return [{"key": c["key"], "label": c["label"],
             "enabled": bool(c.get("enabled")),
             "mock": not bool(c.get("enabled"))}
            for c in all_configs()]
