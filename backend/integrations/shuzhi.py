"""数智人大适配。

《设计方案》五：获取姓名、学号、手机、身份证号；活动结束后回传时长。
对接方式二选一（由学校信息化部门确定）：
  · 只读 API 实时查询；
  · 每日同步宽表（离线同步）——Mock 即按此模式模拟：直接读本地 users 表，
    视作"昨日同步下来的宽表"。
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from ..database import SessionLocal
from ..models import HoursRecord, User
from . import get_config, is_mock


@dataclass
class PersonProfile:
    """身份信息宽表的一行。"""
    student_id: str
    name: str
    gender: str = ""
    college: str = ""
    grade: str = ""
    phone: str = ""
    email: str = ""
    id_card: str = ""
    source: str = "mock"          # mock / shuzhi_renda


@dataclass
class UploadReceipt:
    ok: bool
    receipt_no: str = ""
    message: str = ""
    count: int = 0
    source: str = "mock"


def get_person(student_id: str) -> PersonProfile | None:
    """按学号取身份。演示模式从本地库取；真实模式调数智人大接口。"""
    if is_mock("shuzhi_renda"):
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.student_id == student_id).first()
            if u is None:
                return None
            return PersonProfile(
                student_id=u.student_id, name=u.name, gender=u.gender,
                college=u.college, grade=u.grade, phone=u.phone,
                email=u.email, id_card=u.id_card)
        finally:
            db.close()
    cfg = get_config("shuzhi_renda")
    url = f"{cfg['base_url'].rstrip('/')}/person/{student_id}"
    req = urllib.request.Request(url, headers={"App-Key": cfg.get("app_key", "")})  # noqa: S310
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
        d = json.loads(resp.read().decode())
    return PersonProfile(source="shuzhi_renda", **{
        k: d.get(k, "") for k in ("student_id", "name", "gender", "college",
                                  "grade", "phone", "email", "id_card")})


def upload_service_hours(activity_id: str, records: list[HoursRecord]) -> UploadReceipt:
    """回传志愿时长。返回回执号，写入 hours_records.upload_ref。"""
    if is_mock("shuzhi_renda"):
        return UploadReceipt(ok=True, receipt_no=f"MOCK-{activity_id[:6].upper()}-"
                             f"{len(records):04d}", count=len(records),
                             message="演示模式：已生成模拟回执，未真正外发")
    cfg = get_config("shuzhi_renda")
    body = json.dumps([{"student_id": "", "hours": r.hours}
                       for r in records]).encode()
    req = urllib.request.Request(
        f"{cfg['base_url'].rstrip('/')}/service-hours/upload",
        data=body, headers={"Content-Type": "application/json",
                            "App-Key": cfg.get("app_key", "")})  # noqa: S310
    with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
        d = json.loads(resp.read().decode())
    return UploadReceipt(ok=bool(d.get("ok")), receipt_no=d.get("receipt_no", ""),
                         message=d.get("message", ""), count=len(records),
                         source="shuzhi_renda")
