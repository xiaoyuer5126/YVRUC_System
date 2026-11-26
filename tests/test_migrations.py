"""数据库迁移。

表结构只由迁移建、只由迁移改。这几条用例钉住：
- 迁移跑完的结构就是模型声明的结构（改了模型忘写迁移，这里红）；
- 空库升到最新，和直接按模型建表，得到的表、列、索引、约束一模一样；
- 迁移只有一个 head；
- 库版本不对时后端拒绝启动；
- 几个进程同时跑迁移，只有一个真去建表，其余等它跑完；
- 空库升到最新就有校青协的 12 个部门，库里已有部门时迁移不动它们。
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend import database, migrate
from backend.database import Base, engine
from conftest import ADMIN, login

TEST_URL = make_url(os.environ["YVRUC_DATABASE_URL"])


@contextmanager
def scratch_db(suffix: str):
    """临时建一个空库，用完删掉。名字带上测试库名，免得和别的任务撞上。"""
    name = f"{TEST_URL.database}_{suffix}"
    admin = create_engine(TEST_URL.set(database="postgres"),
                          isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(TEST_URL.set(database=name), future=True)
    try:
        yield eng
    finally:
        eng.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def structure(eng) -> dict[str, list]:
    """从系统目录读出 public 下的完整结构，迁移自己的版本表除外。"""
    skip = "alembic_version"
    with eng.connect() as conn:
        columns = conn.execute(text("""
            SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod),
                   a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> :skip
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY 1, 2"""), {"skip": skip}).all()
        indexes = conn.execute(text("""
            SELECT tablename, indexname, indexdef FROM pg_indexes
            WHERE schemaname = 'public' AND tablename <> :skip
            ORDER BY 1, 2"""), {"skip": skip}).all()
        constraints = conn.execute(text("""
            SELECT c.relname, k.conname, pg_get_constraintdef(k.oid)
            FROM pg_constraint k
            JOIN pg_class c ON c.oid = k.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname <> :skip
            ORDER BY 1, 2"""), {"skip": skip}).all()
    return {"columns": [tuple(r) for r in columns],
            "indexes": [tuple(r) for r in indexes],
            "constraints": [tuple(r) for r in constraints]}


def test_迁移后的库与模型一致():
    """等价于 alembic check：改了模型却没写迁移，这里会列出差在哪。"""
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True,
                                                     "compare_server_default": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == []


def test_空库升级与按模型建表结构完全一致():
    with scratch_db("mig") as migrated, scratch_db("ca") as created:
        migrate.upgrade(migrated)
        Base.metadata.create_all(created)
        got, want = structure(migrated), structure(created)
    assert want["columns"], "按模型建表却什么都没读到，比对就失去意义了"
    assert any("WHERE" in r[2] for r in want["indexes"]), "部分唯一索引也要比到"
    assert got == want


def test_迁移只有一个head且测试库在最新版本():
    assert migrate.head_revision() == migrate.current_revision()


def test_库版本不对时拒绝启动():
    head = migrate.head_revision()
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'stale'"))
    try:
        with pytest.raises(RuntimeError, match="python -m backend.migrate"):
            database.check_schema()
    finally:
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :v"),
                         {"v": head})
    database.check_schema()


def test_没有版本记录的库拒绝启动():
    with scratch_db("bare") as bare:
        Base.metadata.create_all(bare)
        with pytest.raises(RuntimeError, match="没有版本记录"):
            database.check_schema(bare)


def test_并发跑迁移只建一次表():
    with scratch_db("race") as eng:
        errors: list[BaseException] = []

        def run():
            try:
                migrate.upgrade(eng)
            except BaseException as e:  # noqa: BLE001  线程里的异常要带回主线程判
                errors.append(e)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert migrate.current_revision(eng) == migrate.head_revision()


DEPARTMENTS = ["对外联络部", "办公室", "新媒体技术部", "组织部", "支教项目部",
               "红十字项目部", "环保项目部", "青春健康项目部", "社区服务部",
               "特殊关爱项目部", "校园服务部", "首都服务部"]
LIAISON_NOTE = "【二级志愿团体请选择此部门审批】"


def departments(eng) -> list[tuple[str, str]]:
    with eng.connect() as conn:
        return [tuple(r) for r in conn.execute(text(
            "SELECT name, note FROM departments ORDER BY sort"))]


def upgrade_to(eng, revision: str) -> None:
    cfg = migrate.alembic_config()
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def test_空库升到最新就有12个部门():
    with scratch_db("dept") as eng:
        migrate.upgrade(eng)
        got = departments(eng)
    assert [name for name, _ in got] == DEPARTMENTS
    assert got[0][1] == LIAISON_NOTE
    assert all(note == "" for _, note in got[1:])


def test_库里已有部门时迁移不铺部门():
    with scratch_db("dept_kept") as eng:
        upgrade_to(eng, "0002")
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO departments (id, name, note, sort) "
                              "VALUES ('dep_manual', '手工建的部', '', 0)"))
        migrate.upgrade(eng)
        assert departments(eng) == [("手工建的部", "")]


def test_部门列表接口给出迁移铺好的部门(client):
    # 测试库是各用例共用的，别的用例会改部门说明、加删部门，这里只核对 12 个部门
    # 都在且顺序不变；说明的取值由上面的空库用例核对
    rows = client.get("/api/staff/departments",
                      headers=login(client, ADMIN)).json()
    assert [d["name"] for d in rows if d["name"] in DEPARTMENTS] == DEPARTMENTS


# ======================= 0024：拆开挂了多个岗位的普通场次 =======================
#
# 岗位自带时段之后，service/pick 的场次恰好装一个岗位。这一组用例在「升到 0023
# 的库」里造出老形状的数据（一个场次三个岗位），跑完迁移再逐项核对搬没搬对：
# 0024 只动数据、不动表结构，所以造数据和读回来都能直接用模型。

def _old_shape(session):
    """老形状：一个 pick 场次（两段时段）+ 三个岗位，带报名、意向、签到码、
    签到记录、时长明细与场次级学习材料。返回要在断言里用到的那些 id。"""
    from datetime import timedelta

    from backend.models import (Activity, Application, Attachment, CheckinGeo,
                                CheckinRecord, CheckinTask, Enrollment,
                                HoursRecord, HoursSegment, Position, Shift,
                                ShiftPeriod, User, day_of, now, uid)

    base = now().replace(microsecond=0) + timedelta(days=20)
    users = []
    for i in range(4):
        u = User(id=uid("u"), student_id=f"7{i:06d}", name=f"拆分{i}",
                 user_type="student", roles=["student"])
        session.add(u)
        users.append(u)
    # 先把人写进去：活动的 created_by 是外键，没有对应的关系属性，
    # 同一次 flush 里 SQLAlchemy 不保证先插用户
    session.flush()
    act = Activity(id=uid("act"), title="拆分测试", intro="x", location="世纪馆",
                   contacts=[{"name": "王", "phone": "13900000000"}],
                   contact_group_qr="x", status="published",
                   reg_start=base - timedelta(days=10),
                   reg_end=base - timedelta(days=1), created_by=users[0].id)
    session.add(act)
    g = Shift(id=uid("g"), activity_id=act.id, name="运动会当天",
              mode="pick", kind="service", location="北门", note="七点集合",
              hours_mode="fixed", lock_hours=24, enroll_paused=True,
              list_locked=True, auto_lock_spent=True, leader_alert_sent=True,
              digest_sent_on=day_of(base), flexible_time=False, sort=3,
              requirement_tags={"校区": ["中关村校区", "苏州校区"]})
    session.add(g)
    periods = []
    for i in range(2):
        p = ShiftPeriod(id=uid("sp"), shift_id=g.id, sort=i,
                        start_time=base + timedelta(days=i),
                        end_time=base + timedelta(days=i, hours=3),
                        reminder_sent_at=base - timedelta(days=2))
        session.add(p)
        periods.append(p)
    kept, moved_b, moved_c = (
        Position(id=uid("p"), shift_id=g.id, name=name, quota=3, sort=i,
                 service_task="现场服务", training_note="到岗讲解",
                 location="" if i == 0 else "南门",
                 requirement_tags={"性别": ["女"]} if i == 0 else {})
        for i, name in enumerate(("甲", "乙", "丙")))
    session.add_all([kept, moved_b, moved_c])
    session.flush()

    # 正式报名在「乙」，候补在「丙」
    session.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                           user_id=users[0].id, position_id=moved_b.id,
                           status="enrolled", joined_at=now()))
    session.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                           user_id=users[1].id, position_id=moved_c.id,
                           status="waitlisted", joined_at=now()))
    # 申请中的意向：勾了「甲」和「丙」，意向记录上写的是首选「甲」
    session.add(Application(id=uid("app"), activity_id=act.id,
                            user_id=users[2].id, answers={},
                            wanted_positions=[kept.id, moved_c.id]))
    session.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                           user_id=users[2].id, position_id=kept.id,
                           status="applied", joined_at=now()))
    # 没写岗位的意向：整场都行，拆开后每个岗位都该有一条
    session.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                           user_id=users[3].id, position_id=None,
                           status="applied", joined_at=now()))

    whole = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                        period_id=periods[0].id, mode="shift", token="AAA111",
                        created_by=users[0].id,
                        valid_from=periods[0].start_time,
                        valid_to=periods[0].end_time)
    session.add(whole)
    session.add(CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                            period_id=periods[1].id, position_id=moved_b.id,
                            mode="position", token="BBB222",
                            created_by=users[0].id))
    session.flush()
    session.add(CheckinGeo(id=uid("geo"), task_id=whole.id, lat=39.9, lng=116.3,
                           radius_m=100, set_by=users[0].id))
    # 第一条认得出岗位；第二条是扫整场统一码留下的，只能按报名记录认人
    session.add(CheckinRecord(id=uid("ckr"), task_id=whole.id,
                              activity_id=act.id, shift_id=g.id,
                              period_id=periods[0].id, position_id=moved_b.id,
                              user_id=users[0].id, method="manual"))
    session.add(CheckinRecord(id=uid("ckr"), task_id=whole.id,
                              activity_id=act.id, shift_id=g.id,
                              period_id=periods[1].id, position_id=None,
                              user_id=users[0].id, method="manual"))
    rec = HoursRecord(id=uid("hr"), activity_id=act.id, user_id=users[0].id,
                      hours=3.0)
    session.add(rec)
    session.flush()
    session.add(HoursSegment(id=uid("seg"), record_id=rec.id,
                             activity_id=act.id, user_id=users[0].id,
                             kind="shift", seq=0, date=day_of(base), hours=3.0,
                             position_name="乙", time_text="第一段",
                             shift_id=g.id, period_id=periods[0].id,
                             position_id=moved_b.id))
    session.add(Attachment(id=uid("att"), owner_type="shift", owner_id=g.id,
                           activity_id=act.id, kind="link", title="场地图",
                           url="https://v.ruc.edu.cn/x"))
    session.commit()
    return {"activity": act.id, "shift": g.id, "periods": [p.id for p in periods],
            "spans": [(p.start_time, p.end_time) for p in periods],
            "kept": kept.id, "moved": [moved_b.id, moved_c.id],
            "users": [u.id for u in users], "whole_task": whole.id}


@pytest.fixture(scope="module")
def split_result():
    """在 0023 的库里造老形状的数据，跑完迁移把库交给下面几条用例读。"""
    from sqlalchemy.orm import Session

    with scratch_db("split") as eng:
        upgrade_to(eng, "0023")
        with Session(eng) as session:
            ids = _old_shape(session)
        migrate.upgrade(eng)
        with Session(eng) as session:
            yield session, ids


def _shifts_of(session, activity_id):
    from backend.models import Shift
    return {g.name: g for g in session.query(Shift)
            .filter(Shift.activity_id == activity_id).all()}


def test_迁移_一岗一场且场次名跟岗位名一致(split_result):
    session, ids = split_result
    shifts = _shifts_of(session, ids["activity"])
    assert sorted(shifts) == ["丙", "乙", "甲"]
    for name, g in shifts.items():
        assert [p.name for p in g.positions] == [name]
    assert shifts["甲"].id == ids["shift"], "第一个岗位留在原场次"


def test_迁移_新场次复制了全部字段与时段(split_result):
    session, ids = split_result
    shifts = _shifts_of(session, ids["activity"])
    old = shifts["甲"]
    for name in ("乙", "丙"):
        g = shifts[name]
        assert g.id != old.id
        for key in ("activity_id", "mode", "kind", "note", "hours_mode",
                    "lock_hours", "enroll_paused", "list_locked",
                    "auto_lock_spent", "leader_alert_sent", "digest_sent_on",
                    "flexible_time"):
            assert getattr(g, key) == getattr(old, key), (name, key)
        assert g.sort > old.sort, "拆出来的排在后面，sort 不跟别人撞"
        assert [(p.start_time, p.end_time) for p in g.sorted_periods()] \
            == ids["spans"]
        assert {p.id for p in g.periods} & set(ids["periods"]) == set(), \
            "时段是新建的行，不是把原来那几行搬过去"
        assert all(p.reminder_sent_at is not None for p in g.periods), \
            "签到提醒发没发跟着复制，不再提醒一遍"


def test_迁移_报名与意向跟着岗位走(split_result):
    from backend.models import Enrollment
    session, ids = split_result
    shifts = _shifts_of(session, ids["activity"])
    rows = session.query(Enrollment).filter(
        Enrollment.activity_id == ids["activity"]).all()
    by_user = {}
    for row in rows:
        by_user.setdefault(row.user_id, []).append(row)
    u1, u2, u3, u4 = ids["users"]

    [enrolled] = by_user[u1]
    assert (enrolled.status, enrolled.shift_id) == ("enrolled", shifts["乙"].id)
    [waiting] = by_user[u2]
    assert (waiting.status, waiting.shift_id) == ("waitlisted", shifts["丙"].id)
    # 勾了「甲」「丙」：首选留在原场次，「丙」在新场次补一条意向
    assert {(r.shift_id, r.position_id) for r in by_user[u3]} == {
        (shifts["甲"].id, ids["kept"]),
        (shifts["丙"].id, ids["moved"][1])}
    assert all(r.status == "applied" for r in by_user[u3])
    # 没写岗位的意向：三个岗位各一条，岗位写上去
    assert {(r.shift_id, r.position_id) for r in by_user[u4]} == {
        (shifts[name].id, g.positions[0].id) for name, g in shifts.items()}


def test_迁移_签到码与签到记录时长段都搬过去(split_result):
    from backend.models import CheckinGeo, CheckinRecord, CheckinTask, HoursSegment
    session, ids = split_result
    shifts = _shifts_of(session, ids["activity"])
    tasks = session.query(CheckinTask).filter(
        CheckinTask.activity_id == ids["activity"]).all()

    whole = [t for t in tasks if t.mode == "shift"]
    assert len(whole) == 3, "整场统一码给每个新场次各复制一行"
    assert {t.shift_id for t in whole} == {g.id for g in shifts.values()}
    assert len({t.code_group for t in whole}) == 1, "共用一个 code_group"
    assert all(t.code_group for t in whole), "原来那行也补上了 code_group"
    assert len({t.token for t in whole}) == 3, "固定码各是一串"
    first = {g.id: g.sorted_periods()[0].id for g in shifts.values()}
    assert {t.shift_id: t.period_id for t in whole} == first, \
        "时段按起止时间对到新场次自己那一段"
    # 锚点跟着码走：三张码各有一行
    assert session.query(CheckinGeo).filter(
        CheckinGeo.task_id.in_([t.id for t in whole])).count() == 3

    [mine] = [t for t in tasks if t.mode == "position"]
    assert mine.shift_id == shifts["乙"].id
    assert mine.period_id == shifts["乙"].sorted_periods()[1].id

    records = session.query(CheckinRecord).filter(
        CheckinRecord.activity_id == ids["activity"]).all()
    assert {r.shift_id for r in records} == {shifts["乙"].id}, \
        "认得出岗位的按岗位走，扫整场码的按报名记录认人"
    assert sorted(r.period_id for r in records) \
        == sorted(p.id for p in shifts["乙"].periods)

    [seg] = session.query(HoursSegment).filter(
        HoursSegment.activity_id == ids["activity"]).all()
    assert seg.shift_id == shifts["乙"].id
    assert seg.period_id == shifts["乙"].sorted_periods()[0].id


def test_迁移_场次级材料与条件地点收口到岗位(split_result):
    from backend.models import Attachment
    session, ids = split_result
    shifts = _shifts_of(session, ids["activity"])

    owners = [a.owner_id for a in session.query(Attachment).filter(
        Attachment.owner_type == "shift").all()]
    assert sorted(owners) == sorted(g.id for g in shifts.values()), \
        "挂在场次上的学习材料复制给每个新场次"

    for g in shifts.values():
        assert g.requirement_tags == {} and g.location == ""
    from backend.models import Position
    kept = session.get(Position, ids["kept"])
    # 岗位原来写了性别，场次写了校区：两份并到岗位上
    assert kept.requirement_tags == {"性别": ["女"],
                                     "校区": ["中关村校区", "苏州校区"]}
    assert kept.location == "北门", "岗位没写地点，搬场次那一份过来"
    assert shifts["乙"].positions[0].location == "南门", "岗位自己写了的不动"
