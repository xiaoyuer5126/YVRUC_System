"""活动级汇总导出（数据总览页的「导出活动汇总」）。

口径：进表的是报名中/已截止/已结束等活动（SUMMARY_STATUSES），含不公开招募
（补记录）活动。区间按活动结束时间算（end 含当天整天），一场活动的人数、人次、
时长整场算在这一行，办完再补录的时长也跟着活动走。人数按人去重、人次按报名
记录条数；补记录活动没有场次、不收报名，人数与人次是 0。合计行下面空一行是
两行口径说明（见 _rows）。

活动都排在 2019 年，一个用例占一个月：种子与别的用例的活动都照 now() 现算，
那个窗口里只有本文件造的几场，合计行才断言得准。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import unquote

from openpyxl import load_workbook

from backend.models import (Activity, AuditLog, Enrollment, HoursRecord,
                            Notification, Shift, day_of, now, uid)
from conftest import ADMIN, dl_url, login, student_row

COUNCIL = "2025003"    # 张远航，会长团：数据总览这一页的另一个身份
HEAD = "2025004"       # 周砚，对外联络部部长：进不了数据总览
MEMBER = "2025006"     # 苏昀，对外联络部成员（学习权限）
PATH = "/api/staff/activities-summary.xlsx"

HEADER = ["活动编号", "活动名称", "组织单位", "联合主办方", "时间", "状态",
          "志愿者人数", "人次", "志愿时数", "劳务时数"]


def dt(text: str) -> datetime:
    """2019 年里的一个本地时间：'05-06 09:00' → 2019-05-06 09:00。"""
    return datetime.strptime(f"2019-{text}", "%Y-%m-%d %H:%M")


def _activity(db, title, start=None, end=None, *, status="finished",
              organizer="校青协", co_organizer="", mode="instant", shift=True):
    """一场活动：招募活动带一个服务场次，补记录活动把时间填在 held_* 上，
    shift=False 的排不出场次（time_span() 为空）。返回（活动, 场次）。"""
    act = Activity(id=uid("act"), title=title, intro="用例生成",
                   location="世纪馆", organizer=organizer,
                   co_organizer=co_organizer, recruit_mode=mode,
                   status=status, contact_group_qr="x",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   reg_start=(start or now()) - timedelta(days=7),
                   reg_end=start or now(), created_by=student_row(db, ADMIN).id)
    db.add(act)
    db.flush()
    g = None
    if mode == "record":
        act.held_start, act.held_end = start, end
    elif shift:
        g = Shift(id=uid("g"), activity_id=act.id, name="上午场", mode="pick",
                  start_time=start, end_time=end, sort=0)
        db.add(g)
    db.commit()
    return act, g


def _enroll(db, act, g, sid, status="enrolled"):
    """把一个人放进这场活动。走库而不是报名接口：这里要的是现成的报名记录。"""
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=student_row(db, sid).id, status=status,
                      joined_at=now()))
    db.commit()


def _seg(hours, day, *, kind="shift", reward="", label="", position=""):
    """一段明细：字段名就是列名（段上没有界面那套 time / position 键名）。"""
    return {"kind": kind, "date": day_of(day), "hours": hours, "reward": reward,
            "label": label or kind, "position_name": position,
            "time_text": f"{day:%m-%d}"}


def _hours(db, act, sid, *segs):
    """这个人在活动里的一笔时长：段一段段加上去，合计由 before_flush 钩子算出来。"""
    rec = HoursRecord(id=uid("hrs"), activity_id=act.id,
                      user_id=student_row(db, sid).id)
    db.add(rec)
    for fields in segs:
        rec.add_segment(**fields)
    db.commit()
    return rec


def _rows(client, headers, query=""):
    """导出表：响应、表头、数据行、合计行、表底说明（两行）。

    空着的格子读出来是 None：openpyxl 把空串写成没有内容的 <c>，读回来
    分不出「空串」和「这个格子没写过」。断言里空着一律写 ""。

    说明写在合计行下面的表底，这里连结构一起核：数据行仍旧从第 2 行开始读，
    拿到表的人不必先跳过说明才找得到数据。
    """
    r = client.get(dl_url(client, headers, f"{PATH}{query}"))
    assert r.status_code == 200, r.text
    sheet = [["" if v is None else v for v in row]
             for row in load_workbook(BytesIO(r.content)).active.values]
    assert sheet[-4][0] == "合计", sheet[-5:]
    assert not any(sheet[-3]), sheet[-3:]          # 合计行下面空一行
    assert all(row[0] for row in sheet[-2:])       # 表底两行是口径说明
    return r, sheet[0], sheet[1:-4], sheet[-4], sheet[-2:]


def _row(rows, act):
    got = [row for row in rows if row[0] == act.id]
    assert len(got) == 1, f"「{act.title}」在表里应恰好一行，实际 {len(got)} 行"
    return got[0]


def test_表头顺序与合计行(client, db, take_student):
    """列名与顺序固定；末行合计人次与时数，人数不去重就不合计。"""
    council = login(client, COUNCIL)
    first, g1 = _activity(db, "汇总五月甲", dt("05-06 09:00"), dt("05-06 12:00"))
    second, g2 = _activity(db, "汇总五月乙", dt("05-07 14:00"), dt("05-07 17:00"),
                           organizer="文学院青协", co_organizer="校青协")
    _enroll(db, first, g1, take_student())
    _enroll(db, first, g1, take_student())
    _enroll(db, second, g2, take_student())
    _hours(db, first, take_student(), _seg(3, g1.start_time))
    _hours(db, second, take_student(), _seg(1.5, g2.start_time))

    _r, header, rows, total, _notes = _rows(client, council,
                                            "?start=2019-05-01&end=2019-05-31")
    assert header == HEADER
    # 按活动结束时间早晚排，上半月那场在前
    assert [row[0] for row in rows] == [first.id, second.id]
    # 组织单位与联合主办方各占一列：没合办的那场，联办那一格是空串
    assert rows[0] == [first.id, "汇总五月甲", "校青协", "", "05-06（周一）09:00-12:00",
                       "已结束", 2, 2, 3.0, 0.0]
    assert rows[1] == [second.id, "汇总五月乙", "文学院青协", "校青协",
                       "05-07（周二）14:00-17:00", "已结束", 1, 1, 1.5, 0.0]
    # 合计的是人次（2+1）与时数（3+1.5）；人数不去重，不合计，那一格空着
    assert total[0] == "合计" and total[7:] == [3, 4.5, 0.0]
    assert not any(total[1:7])


def test_人数按人去重而人次是报名条数(client, db, take_student):
    """一个人报两个场次：人数算一个、人次算两个（都数服务场次的报名）。"""
    admin = login(client, ADMIN)
    act, morning = _activity(db, "汇总六月两场", dt("06-03 09:00"), dt("06-03 12:00"))
    afternoon = Shift(id=uid("g"), activity_id=act.id, name="下午场", mode="pick",
                      start_time=dt("06-03 14:00"), end_time=dt("06-03 17:00"),
                      sort=1)
    db.add(afternoon)
    db.commit()

    both = take_student()
    another = take_student()
    third = take_student()
    _enroll(db, act, morning, both)
    _enroll(db, act, afternoon, both)
    _enroll(db, act, morning, another)
    _enroll(db, act, afternoon, another)
    _enroll(db, act, morning, third)              # 第三个人只报上午场
    # 退选与候补都不算：既不是志愿者人数，也不是人次
    _enroll(db, act, morning, take_student(), status="cancelled")
    _enroll(db, act, afternoon, take_student(), status="waitlisted")
    _hours(db, act, both, _seg(2, morning.start_time))

    _r, _header, rows, total, _notes = _rows(client, admin,
                                             "?start=2019-06-01&end=2019-06-30")
    row = _row(rows, act)
    assert (row[6], row[7]) == (3, 5)
    assert total[7] == 5 and not total[6]        # 人数不合计


def test_选了劳务的那一段只进劳务列(client, db, take_student):
    """劳务段不计志愿时数；两半在一笔之内各自取整，进各自的列。"""
    council = login(client, COUNCIL)
    act, g = _activity(db, "汇总七月劳务", dt("07-08 09:00"), dt("07-08 13:00"))
    _enroll(db, act, g, take_student())
    # 4.5 小时里选了劳务的有 2 小时：志愿那一半 2.5、劳务那一半 2.0
    assert HoursRecord.settle([(2.0, True), (2.5, False)]) == (2.5, 2.0)
    _hours(db, act, take_student(),
           _seg(2, g.start_time, reward="pay", label="上午场"),
           _seg(2.5, g.start_time, label="上午场"))
    _hours(db, act, take_student(), _seg(3, g.start_time))   # 另一笔全是志愿时长

    _r, _header, rows, total, _notes = _rows(client, council,
                                             "?start=2019-07-01&end=2019-07-31")
    row = _row(rows, act)
    assert (row[8], row[9]) == (5.5, 2.0)
    assert (total[8], total[9]) == (5.5, 2.0)


def test_区间按结束时间算且end含当天整天(client, db):
    """结束在区间里的进；只是跟区间有交集的不进（用户定的是按结束时间）。"""
    admin = login(client, ADMIN)
    inside = _activity(db, "八月里", dt("08-10 09:00"), dt("08-10 12:00"))[0]
    before = _activity(db, "七月末", dt("07-31 09:00"), dt("07-31 12:00"))[0]
    over = _activity(db, "八月开始九月结束",
                     dt("08-31 22:00"), dt("09-01 02:00"))[0]
    cross = _activity(db, "七月开始八月结束",
                      dt("07-31 22:00"), dt("08-01 02:00"))[0]
    last = _activity(db, "八月最后一刻", dt("08-31 20:00"), dt("08-31 23:59"))[0]
    zero = _activity(db, "九月零点整结束", dt("08-31 22:00"), dt("09-01 00:00"))[0]

    _r, _header, rows, _total, _notes = _rows(client, admin,
                                      "?start=2019-08-01&end=2019-08-31")
    ids = {row[0] for row in rows}
    # 结束落在八月里的进：八月里办的，以及七月开始、八月才结束的
    assert {inside.id, cross.id, last.id} <= ids
    # 八月开始、九月结束的只是「跟八月有交集」，按结束时间不算在八月
    assert over.id not in ids
    assert before.id not in ids
    # end 含当天整天：8/31 23:59 结束的算在内，9/1 00:00 整结束的不算
    assert zero.id not in ids

    # 只给 start：结束时间不早于它的都进
    _r, _header, rows, _total, _notes = _rows(client, admin, "?start=2019-09-01")
    ids = {row[0] for row in rows}
    assert {over.id, zero.id} <= ids and inside.id not in ids

    # 只给 end：8/1 00:00 之前结束的才进
    _r, _header, rows, _total, _notes = _rows(client, admin, "?end=2019-07-31")
    ids = {row[0] for row in rows}
    assert before.id in ids
    assert {inside.id, cross.id, over.id, zero.id}.isdisjoint(ids)

    # 都不给：不限，本文件造过的都在
    _r, _header, rows, _total, _notes = _rows(client, admin)
    ids = {row[0] for row in rows}
    assert {inside.id, before.id, over.id, cross.id, last.id, zero.id} <= ids


def test_没有全权的人拿不到这张表(client, take_student):
    student = login(client, take_student())
    for who in (login(client, HEAD), login(client, MEMBER), student):
        r = client.get(dl_url(client, who, PATH))
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "需要 管理员/会长团 身份"


def test_补记录活动进表草稿不进表且导出留痕(client, db, take_student):
    council = login(client, COUNCIL)
    rec, _g = _activity(db, "汇总十月补记录", dt("10-12 14:00"), dt("10-12 17:00"),
                        mode="record")
    _activity(db, "汇总十月草稿", dt("10-15 09:00"), dt("10-15 12:00"),
              status="draft")
    _activity(db, "汇总十月没场次", dt("10-16 09:00"), dt("10-16 12:00"),
              shift=False)
    _hours(db, rec, take_student(), _seg(3, dt("10-12 14:00"), kind="import",
                                         label="补记录"))

    notes = db.query(Notification).count()
    r, header, rows, total, _notes = _rows(client, council,
                                           "?start=2019-10-01&end=2019-10-31")
    assert header == HEADER
    # 补记录活动没有场次也没有报名，时间取 held_*，导入的时长照旧进表
    assert [row[0] for row in rows] == [rec.id]
    assert rows[0] == [rec.id, "汇总十月补记录", "校青协", "", "10-12（周六）14:00-17:00",
                       "已结束", 0, 0, 3.0, 0.0]
    assert total[0] == "合计" and total[7:] == [0, 3.0, 0.0]
    assert not any(total[1:7])

    # 留痕写文件名和条数；导出不发任何通知
    db.expire_all()
    entry = (db.query(AuditLog)
             .filter(AuditLog.action == "export",
                     AuditLog.summary.contains("2019-10-01_2019-10-31"))
             .order_by(AuditLog.created_at.desc()).first())
    assert entry is not None and entry.target_label == "活动汇总"
    assert "《活动汇总_2019-10-01_2019-10-31.xlsx》，1 个活动" in entry.summary
    assert db.query(Notification).count() == notes
    assert "活动汇总_2019-10-01_2019-10-31.xlsx" in unquote(
        r.headers["content-disposition"])

    # 日期格式不对：当场回绝，不吐半张表
    bad = client.get(dl_url(client, council, f"{PATH}?start=2019-13-01"))
    assert bad.status_code == 400 and bad.json()["detail"] == "开始时间格式不对"
    bad = client.get(dl_url(client, council, f"{PATH}?end=2019-02-30"))
    assert bad.status_code == 400 and bad.json()["detail"] == "截止时间格式不对"
