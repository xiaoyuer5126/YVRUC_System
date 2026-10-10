"""时长两半的缓存列：hours_counted / hours_paid 必须始终等于 split() 的结果。

它们只是缓存，真值仍在 hours + parts 上；有了这两列，总览与导出才能把求和交给
SQL，不用把整张台账拉进 Python 逐行拆明细。
"""
from __future__ import annotations

from backend.models import HoursRecord, User, now, uid
from conftest import ADMIN, login


def _record(db, user_id, hours, parts=None):
    rec = HoursRecord(id=uid("hrs"), activity_id=uid("act"), user_id=user_id,
                      hours=hours, parts=parts or [])
    db.add(rec)
    db.commit()
    return rec


PAY_PART = {"kind": "shift", "label": "上午场", "time": "09-20 08:00-12:00",
            "position": "引导", "hours": 2, "reward": "pay"}
HOURS_PART = {"kind": "shift", "label": "上午场", "time": "09-20 08:00-12:00",
              "position": "引导", "hours": 3}


def test_新建时就跟着_split走(client, db, take_student):
    from conftest import student_row

    u = student_row(db, take_student())
    plain = _record(db, u.id, 3)
    assert (plain.hours_counted, plain.hours_paid) == (3.0, 0.0)
    mixed = _record(db, u.id, 4.5, [PAY_PART, HOURS_PART])
    # 4.5 小时里选了劳务的 2 小时向上取整到 0.5 仍是 2
    assert HoursRecord.split(4.5, [PAY_PART, HOURS_PART]) == (2.5, 2.0)
    assert (mixed.hours_counted, mixed.hours_paid) == (2.5, 2.0)


def test_改明细与改合计都会重算(client, db, take_student):
    from conftest import student_row

    u = student_row(db, take_student())
    rec = _record(db, u.id, 4, [HOURS_PART])
    assert (rec.hours_counted, rec.hours_paid) == (4.0, 0.0)
    # 给这一段选上劳务：3 小时的段选了劳务，4 小时里就有 3 小时算劳务
    rec.parts = [{**HOURS_PART, "reward": "pay"}]
    db.commit()
    assert (rec.hours_counted, rec.hours_paid) == (1.0, 3.0)
    # 管理者改合计：增减落在志愿时长那边
    rec.hours = 5
    db.commit()
    assert (rec.hours_counted, rec.hours_paid) == (2.0, 3.0)
    # 到点没表态：各段退回志愿时长
    rec.clear_pay()
    db.commit()
    assert (rec.hours_counted, rec.hours_paid) == (5.0, 0.0)


def test_总览的累计时长等于逐条_split之和(client, db, take_student):
    """缓存列与真值对不上时，这条会红。"""
    admin = login(client, ADMIN)
    from conftest import student_row

    u = student_row(db, take_student())
    _record(db, u.id, 3)
    _record(db, u.id, 4.5, [PAY_PART, HOURS_PART])

    want = sum(HoursRecord.split(h, p)[0] for h, p in
               db.query(HoursRecord.hours, HoursRecord.parts))
    got = client.get("/api/staff/overview", headers=admin).json()["cards"]["hours_total"]
    assert got == round(want, 1)


def test_补记录与手动加的段都算志愿时长(client, db, take_student):
    """只有 kind=shift 且选了 pay 的段算劳务，导入段与手动段全算志愿时长。"""
    from conftest import student_row

    u = student_row(db, take_student())
    rec = _record(db, u.id, 3, [
        {"kind": "import", "label": "补记录", "time": "09-20 08:00-11:00",
         "hours": 3, "reward": "pay"},
        {"kind": "manual", "label": "现场加时", "hours": 0},
    ])
    assert (rec.hours_counted, rec.hours_paid) == (3.0, 0.0)
