"""活动汇总导出的段口径：行 = 区间内有服务段落入的活动，一笔整笔归到它最后
一次服务那一天所属的区间。

四种情形：
  ① 一笔跨月（两段分别在两个月）：整笔算在最后一次服务那一侧，各区间之和 ==
     这一笔的结算数，取整的零头不会算丢；
  ② 补记录活动没有场次、不收报名：人数与人次是 0，只有时长；
  ③ 人数与人次按报名口径落区间，日期取报名所在场次的开始那天，培训场次不算；
  ④ 口径说明写在合计行下面的表底，数据行仍旧从第 2 行开始。

造数与读表都用 test_export_summary 里那套助手：两个文件量的是同一张口径，
助手只有一份，改了不会各说各的。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO

from openpyxl import load_workbook

from backend.models import Enrollment, HoursRecord, Shift, uid
from conftest import login
from test_export_summary import (COUNCIL, _activity, _enroll, _hours, _row,
                                _rows, _seg, dt)

NOV = "?start=2019-11-01&end=2019-11-30"
DEC = "?start=2019-12-01&end=2019-12-31"


def _hours_of(rows, act) -> float:
    """这场活动在表里的志愿时数；不在表里就是 0。"""
    got = [row for row in rows if row[0] == act.id]
    assert len(got) <= 1, got
    return got[0][8] if got else 0.0


def test_一笔跨月整笔算在最后一次服务那侧(client, db, take_student):
    council = login(client, COUNCIL)
    act, g = _activity(db, "跨月一笔", dt("11-30 09:00"), dt("11-30 12:00"))
    _enroll(db, act, g, take_student())
    # 同一笔：11/30 签到 2.2 小时，12/02 又补录 1.4 小时
    _hours(db, act, take_student(),
           _seg(2.2, g.start_time),
           _seg(1.4, dt("12-02 09:00"), kind="manual", label="补录"))
    # 另一笔只服务在 11 月，归 11 月
    _hours(db, act, take_student(), _seg(1.2, g.start_time))

    _r, _h, nov_rows, _t, _n = _rows(client, council, NOV)
    _r, _h, dec_rows, _t, _n = _rows(client, council, DEC)

    recs = db.query(HoursRecord).filter(HoursRecord.activity_id == act.id).all()
    assert sorted(r.counted_hours() for r in recs) == [1.5, 4.0]
    # 跨月那一笔的 4.0 整笔落在十二月（最后一次服务在 12/02），十一月不重复算
    assert (_hours_of(nov_rows, act), _hours_of(dec_rows, act)) == (1.5, 4.0)
    # 各区间之和 == 各笔结算之和
    assert _hours_of(nov_rows, act) + _hours_of(dec_rows, act) == 5.5


def test_补记录活动只有时数(client, db, take_student):
    council = login(client, COUNCIL)
    _r, _h, _rows_, base_total, _n = _rows(client, council, NOV)

    act, _g = _activity(db, "补记录十一月", dt("11-12 14:00"), dt("11-12 17:00"),
                        mode="record")
    _hours(db, act, take_student(), _seg(3, dt("11-12 14:00"), kind="import",
                                         label="场地布置"))

    _r, _h, rows, total, _n = _rows(client, council, NOV)
    row = _row(rows, act)
    # 没有场次、不收报名：人数与人次都是 0，时长照算
    assert (row[6], row[7]) == (0, 0)
    assert (row[8], row[9]) == (3.0, 0.0)
    assert round(total[8] - base_total[8], 1) == 3.0


def test_报名人数按场次日期落区间(client, db, take_student):
    council = login(client, COUNCIL)
    act, dec = _activity(db, "报名跨月", dt("12-08 09:00"), dt("12-08 12:00"))
    jan_start = datetime(2020, 1, 3, 9, 0)
    jan = Shift(id=uid("g"), activity_id=act.id, name="一月场", mode="pick",
                start_time=jan_start, end_time=jan_start + timedelta(hours=3),
                sort=1)
    # 培训的报名是跟着服务场次自动挂上的，进不了人数与人次
    train = Shift(id=uid("g"), activity_id=act.id, name="培训", kind="training",
                  mode="pick", start_time=dt("12-07 09:00"),
                  end_time=dt("12-07 12:00"), sort=2)
    db.add_all([jan, train])
    db.commit()

    u1, u2, u3 = take_student(), take_student(), take_student()
    _enroll(db, act, dec, u1)
    _enroll(db, act, dec, u2)
    _enroll(db, act, jan, u2)          # 同一个人两个月各报一场
    _enroll(db, act, jan, u3)
    _enroll(db, act, train, take_student())
    _hours(db, act, u1, _seg(2, dec.start_time))
    _hours(db, act, u3, _seg(2, jan_start))

    _r, _h, dec_rows, _t, _n = _rows(client, council, DEC)
    _r, _h, jan_rows, _t, _n = _rows(client,
                                     council, "?start=2020-01-01&end=2020-01-31")
    _r, _h, all_rows, _t, _n = _rows(client, council)

    dec_row, jan_row = _row(dec_rows, act), _row(jan_rows, act)
    # 各区间各数各的：十二月 2 人 2 条报名，一月也是 2 人 2 条
    assert (dec_row[6], dec_row[7]) == (2, 2)
    assert (jan_row[6], jan_row[7]) == (2, 2)
    # 全期只有 3 个人、4 条报名（u2 报了两场，培训那条不算）
    assert (_row(all_rows, act)[6], _row(all_rows, act)[7]) == (3, 4)
    assert db.query(Enrollment).filter(
        Enrollment.activity_id == act.id).count() == 5


def test_口径说明写在合计行下面的表底(client, db, take_student):
    council = login(client, COUNCIL)
    act, g = _activity(db, "说明行十一月", dt("11-20 09:00"), dt("11-20 12:00"))
    _enroll(db, act, g, take_student())
    _hours(db, act, take_student(), _seg(2, g.start_time))

    r, header, rows, total, notes = _rows(client, council, NOV)
    assert len(notes) == 2
    assert notes[0][0].startswith("说明：") and "报名" in notes[0][0]
    assert "0.5" in notes[1][0] and "最后一次服务" in notes[1][0]
    assert total[0] == "合计"

    # 表底的位置：表头第 1 行、数据行紧接其后，合计行下面空一行才是说明
    sheet = [["" if v is None else v for v in row]
             for row in load_workbook(BytesIO(r.content)).active.values]
    assert sheet[0] == header
    assert [row[0] for row in sheet[1:-4]] == [row[0] for row in rows]
    assert act.id in [row[0] for row in rows]
    assert sheet[-4][0] == "合计" and not any(sheet[-3])
    assert [row[0] for row in sheet[-2:]] == [notes[0][0], notes[1][0]]
