"""首次登录建档时按身份类型预置身份：老师是活动发起方，学生是学生。

直接调 `_provision`——它收的是 sso 翻译好的身份字典，不经过学校接口；
回调那条路在这里不走。学号、工号都是凭空编的测试号，用完即删。
"""
from __future__ import annotations

import pytest

from backend.database import SessionLocal
from backend.models import Notification, Session, User
from backend.routers.auth import _provision

TEACHER_SID = "20990001"      # 8 位：教工号
STUDENT_SID = "2099200001"    # 10 位：学号


def _drop_users(*sids: str) -> None:
    """删掉用例建出来的档案，别让多出来的人混进别处的人数统计。"""
    s = SessionLocal()
    try:
        ids = [u.id for u in s.query(User)
               .filter(User.student_id.in_(sids)).all()]
        if ids:
            for table in (Session, Notification):
                s.query(table).filter(table.user_id.in_(ids)).delete(
                    synchronize_session=False)
            s.query(User).filter(User.id.in_(ids)).delete(
                synchronize_session=False)
        s.commit()
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _cleanup():
    _drop_users(TEACHER_SID, STUDENT_SID)
    yield
    _drop_users(TEACHER_SID, STUDENT_SID)


def test_老师首次登录即是活动发起方(db):
    user = _provision(db, {"student_id": TEACHER_SID, "name": "某老师",
                           "user_type": "teacher"})
    assert user.user_type == "teacher"
    assert user.roles == ["initiator"]
    # 老师不参与报名，不需要补手机号等个人信息
    assert user.profile_complete()
    assert user.active_role_label() == "活动发起方"


def test_只给工号时按工号认出老师(db):
    """学校没给身份类型时按工号位数兜底，兜出来的老师同样是发起方。"""
    user = _provision(db, {"student_id": TEACHER_SID, "name": "某老师"})
    assert user.user_type == "teacher"
    assert user.roles == ["initiator"]


def test_学生首次登录是学生(db):
    user = _provision(db, {"student_id": STUDENT_SID, "name": "某同学",
                           "user_type": "student"})
    assert user.user_type == "student"
    assert user.roles == ["student"]
    assert not user.profile_complete()


def test_没有身份的老师不显示成学生():
    """身份被管理员全撤掉的老师，称呼按类型走，不能落到「学生」。"""
    assert User(user_type="teacher", roles=[]).active_role_label() == "教职工"
    assert User(user_type="student", roles=[]).active_role_label() == "学生"
