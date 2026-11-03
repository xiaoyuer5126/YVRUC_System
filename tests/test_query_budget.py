"""SQL 条数不随岗位数、活动数、人数涨（N+1 回归）。

抢报那一刻几百人同时打开同一个活动页、同时刷广场。详情页要是每个岗位各查
一遍（组长、同组、材料……），条数就跟着岗位数涨；广场每张卡片各数一遍人数
也一样。这里造「少」和「多」两份同样形状的数据，比两边的条数：
相等才算过——只比「不超过多少条」的话，每个岗位多一条也能混过去。

名单、导出、群发同理，涨的那一维是人数：单场几百人的活动，每人多查一条就是
几百个来回。这几处量的是读的条数（见 counting），写进去的通知和名单本来就
一人一条。

缓存关掉量：量的是现算那条路径，缓存命中时条数只会更少。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta

import pytest
from sqlalchemy import event

from backend import pubcache
from backend.database import engine
from backend.exporter import build_application_workbook, build_claims_workbook
from backend.models import (Activity, ActivityManager, Application, Attachment,
                            AuditLog, Enrollment, FormQuestion, HoursClaim,
                            Position, Shift, now, uid)
from backend.notify import (notify_checkin, notify_edit_changes,
                            notify_enrolled)
from conftest import mk_shift
from conftest import ADMIN, POSITION_TEXT, login, student_row


@contextmanager
def counting():
    """这一段里执行了多少条 SQL。

    `select` 只数读的那些：发通知、写名单会按人落库，写的条数本来就跟人数
    成正比，混在一起数就看不出读路径有没有逐人查库。
    """
    n = {"sql": 0, "select": 0}

    def _count(_conn, _cur, statement, *_a):
        n["sql"] += 1
        if statement.lstrip()[:6].upper() == "SELECT":
            n["select"] += 1
    event.listen(engine, "before_cursor_execute", _count)
    try:
        yield n
    finally:
        event.remove(engine, "before_cursor_execute", _count)


def sql_of(client, headers, path) -> int:
    with counting() as n:
        r = client.get(path, headers=headers)
    assert r.status_code == 200, r.text
    return n["sql"]


@pytest.fixture
def no_cache(monkeypatch):
    monkeypatch.setattr(pubcache, "ENABLED", False)


_days = iter(range(400, 10_000, 3))


def build(db, take_student, per_shift: int, title: str = "") -> dict:
    """两个服务场次（各带一场培训）、每场 per_shift 个岗位，岗位两两成组、
    各挂一份材料；每组一名组长，另有一名报在第一个岗位上的志愿者。

    返回活动 id 与几个人的学号。场次按天错开，各用例之间不会撞时段。
    """
    admin = student_row(db, ADMIN)
    day = next(_days)
    act = Activity(id=uid("act"), title=title or f"条数 {uid('t')}",
                   intro="x", location="世纪馆", contact_group_qr="x",
                   contacts=[{"name": "王", "phone": "13900000000"}],
                   status="published", reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=day), created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    first_pos = None
    for k in range(2):
        start = now() + timedelta(days=day + k, hours=2)
        g = mk_shift(id=uid("g"), activity_id=act.id, name=f"场次{k}",
                  start_time=start, end_time=start + timedelta(hours=3),
                  mode="pick", sort=k)
        db.add(g)
        db.flush()
        db.add(mk_shift(id=uid("g"), activity_id=act.id, name=f"培训{k}",
                     kind="training",
                     start_time=start - timedelta(hours=2),
                     end_time=start - timedelta(hours=1)))
        for i in range(per_shift):
            p = Position(id=uid("p"), shift_id=g.id, name=f"岗位{i}", quota=5,
                         group_name=f"组{i // 2}", needs_leader=True, sort=i,
                         **POSITION_TEXT)
            db.add(p)
            db.flush()          # 报名记录外键指向岗位，先落岗位
            db.add(Attachment(id=uid("att"), owner_type="position",
                              owner_id=p.id, activity_id=act.id, kind="link",
                              title="手册", url="https://v.ruc.edu.cn/x"))
            first_pos = first_pos or p
            if i % 2 == 0:
                u = student_row(db, take_student())
                db.add(Enrollment(id=uid("enr"), activity_id=act.id,
                                  shift_id=g.id, user_id=u.id,
                                  position_id=p.id, status="enrolled",
                                  is_leader=True, joined_at=now()))
    member = take_student()
    db.add(Enrollment(id=uid("enr"), activity_id=act.id,
                      shift_id=first_pos.shift_id,
                      user_id=student_row(db, member).id,
                      position_id=first_pos.id, status="enrolled",
                      joined_at=now()))
    db.commit()
    return {"id": act.id, "member": member, "outsider": take_student()}


def test_活动详情的条数不随岗位数涨(client, admin, db, take_student, no_cache):
    few = build(db, take_student, per_shift=2)
    many = build(db, take_student, per_shift=10)

    def measure(case):
        path = f"/api/activities/{case['id']}"
        return {
            "外人": sql_of(client, login(client, case["outsider"]), path),
            "已报名": sql_of(client, login(client, case["member"]), path),
            "管理者": sql_of(client, admin, path),
        }

    a, b = measure(few), measure(many)
    assert a == b, (a, b)
    # 顺带钉一个上限，别让它悄悄长上去
    assert max(b.values()) <= 20, b


def test_详情缓存命中时条数更少(client, db, take_student):
    case = build(db, take_student, per_shift=4)
    t = login(client, case["outsider"])
    path = f"/api/activities/{case['id']}"
    cold = sql_of(client, t, path)
    warm = sql_of(client, t, path)
    assert warm < cold, (cold, warm)


def crowd(db, take_student, people: int) -> tuple:
    """一个报名中的活动、一个服务场次、一个岗位、people 名已确认志愿者。

    结构固定、只有人数不同：两份一比，读的条数要是涨了，涨的只能是逐人查库。
    """
    admin = student_row(db, ADMIN)
    day = next(_days)
    start = now() + timedelta(days=day, hours=2)
    act = Activity(id=uid("act"), title=f"群发 {uid('t')}",
                   intro="x", location="世纪馆", contact_group_qr="x",
                   contacts=[{"name": "王", "phone": "13900000000"}],
                   status="published", reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=day), created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    g = mk_shift(id=uid("g"), activity_id=act.id, name="上午场",
                 start_time=start, end_time=start + timedelta(hours=3),
                 mode="pick", sort=0)
    db.add(g)
    db.flush()
    p = Position(id=uid("p"), shift_id=g.id, name="引导", quota=people,
                 group_name="A组", sort=0, **POSITION_TEXT)
    db.add(p)
    db.flush()
    for _ in range(people):
        u = student_row(db, take_student())
        db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                          user_id=u.id, position_id=p.id, status="enrolled",
                          joined_at=now()))
    db.add(AuditLog(id=uid("log"), actor_id=admin.id, actor_name=admin.name,
                    action="update", target_type="activity", target_id=act.id,
                    target_label=act.title, activity_id=act.id,
                    summary="改了集合地点", visible=True,
                    changes=[{"field": "location", "label": "活动地点",
                              "old": "世纪馆", "new": "北门"}]))
    db.commit()
    return act, g


def test_群发通知的条数不随人数涨(db, take_student):
    few = crowd(db, take_student, 5)
    many = crowd(db, take_student, 50)

    def measure(case):
        act, g = case
        out = {}
        for name, call in (
                ("报名者群发", lambda: notify_enrolled(
                    db, act, "activity_changed", activity=act.title,
                    change_summary="集合地点改到北门")),
                ("变更通知", lambda: notify_edit_changes(
                    db, act, now() - timedelta(days=1))),
                ("签到提醒", lambda: notify_checkin(db, g)),
        ):
            with counting() as n:
                sent = call()
            # 发到了人：条数相等但谁也没发出去的话，这个用例什么都没守住
            assert sent > 0, name
            out[name] = n["select"]
            db.rollback()        # 落库的通知不留给下一次测量
        return out

    a, b = measure(few), measure(many)
    assert a == b, (a, b)
    assert max(b.values()) <= 10, b


def screened_crowd(db, take_student, people: int) -> Activity:
    """一个「报名后筛选」的活动、一个场次、两道题（填空 + 文件），
    people 份申请，每份一条意向报名、一个上传的附件。

    附件只写记录不落盘：导出里查附件那一步照样走到，文件在不在只影响单元格
    写不写得出文件名。
    """
    admin = student_row(db, ADMIN)
    day = next(_days)
    start = now() + timedelta(days=day, hours=2)
    act = Activity(id=uid("act"), title=f"筛选 {uid('t')}",
                   intro="x", location="东门", contact_group_qr="x",
                   contacts=[{"name": "王", "phone": "13900000000"}],
                   recruit_mode="screened", status="published",
                   reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=day), created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    g = mk_shift(id=uid("g"), activity_id=act.id, name="第一场",
                 start_time=start, end_time=start + timedelta(hours=3),
                 mode="assign", sort=0)
    db.add(g)
    db.flush()
    p = Position(id=uid("p"), shift_id=g.id, name=Position.PLACEHOLDER_NAME,
                 is_placeholder=True, quota=people, sort=-1, **POSITION_TEXT)
    db.add(p)
    text_q = FormQuestion(id=uid("q"), activity_id=act.id, kind="text",
                          title="介绍自己", required=False, sort=0)
    file_q = FormQuestion(id=uid("q"), activity_id=act.id, kind="file",
                          title="证明材料", required=False, sort=1)
    db.add_all([text_q, file_q])
    db.flush()
    for _ in range(people):
        u = student_row(db, take_student())
        att = Attachment(id=uid("att"), owner_type="application",
                         owner_id=u.id, activity_id=act.id, kind="file",
                         title="证明.pdf", stored_name=f"{uid('f')}.pdf",
                         uploaded_by=u.id)
        db.add(att)
        db.add(Application(id=uid("app"), activity_id=act.id, user_id=u.id,
                           answers={text_q.id: "我很热心", file_q.id: att.id},
                           status="applied"))
        db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                          user_id=u.id, position_id=p.id, status="applied",
                          joined_at=now()))
    db.commit()
    return act, people


def test_报名问卷导出的条数不随人数涨(db, take_student):
    few = screened_crowd(db, take_student, 5)
    many = screened_crowd(db, take_student, 50)

    def measure(case):
        act, people = case
        db.expire_all()
        with counting() as n:
            ex = build_application_workbook(db, act)
        # 行数对得上：表写空了，条数自然也不涨
        assert ex.wb.active.max_row == people + 1
        return n["select"]

    a, b = measure(few), measure(many)
    assert a == b, (a, b)
    assert b <= 15, b


def test_筛选名单的条数不随人数涨(client, admin, db, take_student):
    few = screened_crowd(db, take_student, 5)
    many = screened_crowd(db, take_student, 50)

    def measure(case):
        act, people = case
        path = f"/api/staff/activities/{act.id}/applications"
        with counting() as n:
            r = client.get(path, headers=admin)
        assert r.status_code == 200, r.text
        assert len(r.json()["applications"]) == people
        return n["select"]

    a, b = measure(few), measure(many)
    assert a == b, (a, b)
    assert b <= 25, b


def test_校外时长导出的条数不随条数涨(db, take_student):
    """一人一条通过的校外补登，导出时读的条数要跟条数无关。"""
    def claims(people: int) -> list[str]:
        ids = []
        for _ in range(people):
            u = student_row(db, take_student())
            row = HoursClaim(id=uid("clm"), user_id=u.id, title="社区助老",
                             service_date=date(2026, 3, 1), hours=4.0,
                             approved_hours=4.0, service_desc="陪同就医",
                             status="approved")
            db.add(row)
            ids.append(row.id)
        db.commit()
        return ids

    few, many = claims(5), claims(50)

    def measure(ids):
        with counting() as n:
            # 接口就是「先查出这一批，再交给导出」（见 hours_claims.export_claims）
            rows = db.query(HoursClaim).filter(HoursClaim.id.in_(ids)).all()
            build_claims_workbook(db, rows)
        assert len(rows) == len(ids)
        return n["select"]

    a, b = measure(few), measure(many)
    assert a == b, (a, b)
    assert b <= 5, b


def test_广场列表的条数不随活动数涨(client, admin, db, take_student, no_cache):
    t = login(client, take_student())
    counts = {}
    for n in (2, 8):
        tag = uid("sq")
        for _ in range(n):
            build(db, take_student, per_shift=3, title=f"{tag} 广场")
        counts[n] = (
            sql_of(client, t, f"/api/activities?q={tag}&status=&size=50"),
            # 管理端列表同样是一页一张卡片
            sql_of(client, admin, f"/api/staff/activities?q={tag}&size=50"),
        )
    assert counts[2] == counts[8], counts
