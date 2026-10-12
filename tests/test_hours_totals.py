"""时长的统计口径：一笔的结算数由这一笔的各段算出来，统计侧按笔汇总。

  · 一笔的合计（hours）恒等于各段之和（原始值，见 models 的 before_flush 钩子）；
  · 结算（志愿时长 / 劳务）在一笔之内两半各自向上取整到 0.5（HoursRecord.split）：
    先把两半各自求和再取整，逐段取整会把每一段的零头都攒上去；
  · 数据总览的「累计时长」= 各笔的志愿时数之和，一笔一笔取整之后再相加，
    与活动汇总导出同一口径（见 exporter.activity_hours）。

段本身的口径（三种段、写入顺序、删段、并发）见 test_hours_segments.py。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from backend.models import Activity, HoursRecord, now, uid
from conftest import ADMIN, login, student_row

# 段的服务日期：本地 0 点，2019 年里挑一天，离别的用例的活动都远
DAY = datetime(2019, 9, 20)


def _activity(db) -> Activity:
    """笔要挂在真活动上：段的 activity_id 是指向 activities 的外键。"""
    act = Activity(id=uid("act"), title=f"时长口径 {uid('t')}", intro="用例生成",
                   location="世纪馆", contact_group_qr="x",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="finished", reg_start=now() - timedelta(days=10),
                   reg_end=now() - timedelta(days=5),
                   created_by=student_row(db, ADMIN).id)
    db.add(act)
    db.commit()
    return act


def _seg(kind: str, hours: float, **kw) -> dict:
    """一段明细：字段名就是列名；hours 是原始值，不取整到 0.5。"""
    return {"kind": kind, "date": DAY, "hours": hours, "time_text": "09-20",
            "label": kind, **kw}


def _record(db, act, u, *segs) -> HoursRecord:
    """一笔账：段一段段加进去，合计算出来的（见 models 的 before_flush 钩子）。"""
    rec = HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=u.id)
    db.add(rec)
    for fields in segs:
        rec.add_segment(**fields)
    db.commit()
    return rec


def _card(client, headers) -> float:
    r = client.get("/api/staff/overview", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["cards"]["hours_total"]


def _want(db) -> float:
    """库里每一笔的志愿时数之和：结算只此一处（HoursRecord.split）。"""
    return sum(rec.counted_hours() for rec in db.query(HoursRecord))


def test_一笔之内两半各取一次整(db, take_student):
    """志愿那一半 0.75 取整到 1.0：逐段取整会得到 1.5，零头是这么攒出来的。"""
    act = _activity(db)
    u = student_row(db, take_student())
    rec = _record(db, act, u, _seg("shift", 0.25), _seg("shift", 0.25),
                  _seg("shift", 0.25), _seg("shift", 1.2, reward="pay"))

    assert rec.hours == 1.95                       # 段上留原始值
    assert rec.counted_hours() == 1.0
    assert rec.pay_hours() == 1.5
    assert rec.settled_hours() == 2.5


def test_总览的累计时长等于各笔结算之和(client, db, take_student):
    """总览那张卡另写一套算法就会与逐笔结算对不上，这条会红。"""
    admin = login(client, ADMIN)
    base = _card(client, admin)
    act = _activity(db)
    _record(db, act, student_row(db, take_student()),
            _seg("shift", 0.25), _seg("shift", 0.25))          # 结算 0.5
    _record(db, act, student_row(db, take_student()),
            _seg("shift", 3, reward="pay"), _seg("manual", 2))  # 劳务不计，结算 2.0

    assert round(_card(client, admin) - base, 1) == 2.5
    assert _card(client, admin) == round(_want(db), 1)


def test_总览按笔取整_不把几笔的零头并到一起(client, db, take_student):
    """两笔各 0.5 结算 1.0；把几笔的段并起来只取整一次会算成 0.5。"""
    admin = login(client, ADMIN)
    base = _card(client, admin)
    act = _activity(db)
    for _ in range(2):
        _record(db, act, student_row(db, take_student()),
                _seg("shift", 0.25), _seg("shift", 0.25))

    assert round(_card(client, admin) - base, 1) == 1.0


def test_段一改结算就跟着重算(db, take_student):
    act = _activity(db)
    u = student_row(db, take_student())
    rec = _record(db, act, u, _seg("shift", 2, reward="pay"))
    assert (rec.counted_hours(), rec.pay_hours()) == (0.0, 2.0)

    # 本人把这一段退回志愿时长：合计不变，结算跟着变
    rec.clear_pay()
    db.commit()
    assert rec.hours == 2.0
    assert (rec.counted_hours(), rec.pay_hours()) == (2.0, 0.0)

    # 管理者手动补一段：合计重算，取整仍旧只发生在结算那一步
    rec.add_segment(**_seg("manual", 1.7, label="场外搬运"))
    db.commit()
    assert rec.hours == 3.7
    assert rec.counted_hours() == 4.0
    assert rec.pay_hours() == 0.0


def test_导入段与手动段都只记志愿时长(db, take_student):
    """只有 kind=shift 且选了劳务的段算劳务（见 models.part_is_pay）：
    补记录名单导入与手动补录都是人交进来的账，不按劳务计。"""
    act = _activity(db)
    u = student_row(db, take_student())
    rec = _record(db, act, u,
                  _seg("import", 3, reward="pay", label="补记录"),
                  _seg("manual", 1.2, reward="pay", label="现场加时"))

    assert rec.hours == 4.2                        # 段上的原始值照记
    assert (rec.counted_hours(), rec.pay_hours()) == (4.5, 0.0)
