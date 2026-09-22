"""志愿者档案：只记不罚。

青协能看到一个人参与过的活动、违规记录、临期退选次数；平台不据此拦任何人。
"""
from __future__ import annotations

from datetime import timedelta

from backend.models import Enrollment, User, ViolationRecord, now
from conftest import login, student_row

HEAD, CORE, MEMBER = "2025004", "2025005", "2025006"


def _late_cancel(client, db, open_group, take_student):
    """造一次「临期退选」：报名已截止、距开场不足 24 小时。"""
    act, g = open_group(2, day_offset=0, start_in_hours=5)
    sid = take_student()
    t = login(client, sid)
    assert client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                       headers=t, json={"position_id": g.test_position_id}
                       ).status_code == 200
    act.reg_end = now() - timedelta(minutes=1)
    db.commit()
    enr = (db.query(Enrollment)
           .filter(Enrollment.shift_id == g.id,
                   Enrollment.user_id == student_row(db, sid).id).one())
    r = client.delete(f"/api/activities/enrollments/{enr.id}", headers=t)
    assert r.status_code == 200, r.text
    return act, sid, r.json()


def test_临期退选只扣留名额不扣任何分(client, db, open_group, take_student):
    act, sid, body = _late_cancel(client, db, open_group, take_student)
    assert body["effect"] == "held"
    assert "分" not in body["message"]
    u = student_row(db, sid)
    assert not hasattr(u, "credit_score")
    # 之后照样能报别的活动
    act2, g2 = open_group(3)
    assert client.post(f"/api/activities/{act2.id}/shifts/{g2.id}/enroll",
                       headers=login(client, sid),
                       json={"position_id": g2.test_position_id}).status_code == 200


def test_档案里数得出临期退选次数(client, admin, db, open_group, take_student):
    act, sid, _ = _late_cancel(client, db, open_group, take_student)
    u = student_row(db, sid)
    d = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert d["late_cancel_count"] == 1
    assert any(h["activity_id"] == act.id and h["cancel_type"] == "late"
               for h in d["history"])


def test_登记违规只是记录(client, admin, db, enrolled_activity):
    act, shift, u = enrolled_activity
    r = client.post("/api/staff/violations", headers=admin,
                    json={"user_id": u.id, "activity_id": act.id,
                          "kind": "late", "detail": "迟到 20 分钟"})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert [v["kind_label"] for v in d["violations"]] == ["迟到早退"]
    # 没有任何字段说他被限制了
    assert "restricted_types" not in d["user"]


def test_没报名本活动的人登记不了(client, admin, db, enrolled_activity, take_student):
    act, _shift, _u = enrolled_activity
    other = student_row(db, take_student())
    r = client.post("/api/staff/violations", headers=admin,
                    json={"user_id": other.id, "activity_id": act.id,
                          "kind": "late", "detail": "x"})
    assert r.status_code == 400 and "没有报名本活动" in r.json()["detail"]


def test_撤销违规登记(client, admin, db, enrolled_activity):
    act, _shift, u = enrolled_activity
    vid = client.post("/api/staff/violations", headers=admin,
                      json={"user_id": u.id, "activity_id": act.id,
                            "kind": "other", "detail": "登记错了"}).json()["id"]
    assert client.delete(f"/api/staff/violations/{vid}", headers=admin).status_code == 200
    assert db.get(ViolationRecord, vid) is None


# ======================= 谁能看档案 =======================

def test_全校名册只给管理员会长团部长(client, db, take_student):
    u = student_row(db, take_student())
    assert client.get(f"/api/staff/users/{u.id}/archive",
                      headers=login(client, HEAD)).status_code == 200
    for sid in (CORE, MEMBER):
        assert client.get(f"/api/staff/users/{u.id}/archive",
                          headers=login(client, sid)).status_code == 403, sid


def test_活动管理者只能看报了本活动的人(client, admin, initiator, db,
                                    enrolled_activity, take_student):
    act, _shift, u = enrolled_activity
    url = f"/api/staff/activities/{act.id}/volunteers/{u.id}/archive"
    assert client.get(url, headers=admin).status_code == 200
    # 不是这个活动的管理者：不行
    assert client.get(url, headers=initiator).status_code == 403
    # 是管理者，但对方没报这场：不行
    stranger = student_row(db, take_student())
    assert client.get(f"/api/staff/activities/{act.id}/volunteers/{stranger.id}/archive",
                      headers=admin).status_code == 403


def test_部门成员在活动里也看不了档案(client, db, enrolled_activity):
    from backend.models import ActivityManager, uid

    act, _shift, u = enrolled_activity
    member = db.query(User).filter(User.student_id == MEMBER).one()
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id, user_id=member.id,
                           added_by=member.id))
    db.commit()
    r = client.get(f"/api/staff/activities/{act.id}/volunteers/{u.id}/archive",
                   headers=login(client, MEMBER))
    assert r.status_code == 403


def test_提示分按违规与参与现算(client, admin, db, enrolled_activity):
    """分数不落库：登记一条违规立刻掉分，销掉就长回来。"""
    _act, _shift, u = enrolled_activity
    base = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert base["score"]["score"] == 100 and base["score"]["score_alert"] is False

    ids = []
    for kind in ("contact", "leave", "late", "passive"):
        r = client.post("/api/staff/violations", headers=admin,
                        json={"user_id": u.id, "activity_id": _act.id,
                              "kind": kind, "detail": "演示"})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    d = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert d["score"]["score"] == 100 - 20 - 15 - 10 - 10
    assert d["score"]["score_alert"] is True

    # 名单上这个人要挂感叹号
    roster = client.get(f"/api/staff/shifts/{_shift.id}/roster", headers=admin).json()
    me = next(r for r in roster["enrolled"] if r["user"]["id"] == u.id)
    assert me["user"]["score_alert"] is True and me["user"]["score"] == 45

    client.delete(f"/api/staff/violations/{ids[0]}", headers=admin)
    d = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert d["score"]["score"] == 65 and d["score"]["score_alert"] is False


def test_退选画像统计次数与间隔(client, admin, db, open_group, take_student):
    """近 90 天退了几次、每次是报名多久之后退的——判断是临时有事还是占了就退。"""
    act, g = open_group(3, day_offset=0, start_in_hours=100)
    sid = take_student()
    t = login(client, sid)
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll", headers=t,
                    json={"position_id": g.test_position_id})
    assert r.status_code == 200, r.text
    enr = (db.query(Enrollment)
           .filter(Enrollment.shift_id == g.id,
                   Enrollment.status == "enrolled").one())
    enr.joined_at = now() - timedelta(hours=5)
    db.commit()
    assert client.delete(f"/api/activities/enrollments/{enr.id}",
                         headers=t).status_code == 200

    u = db.query(User).filter(User.student_id == sid).one()
    d = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert d["cancels"]["window_days"] == 90
    assert d["cancels"]["recent_count"] == 1 and d["cancels"]["total_count"] == 1
    item = d["cancels"]["items"][0]
    assert item["cancel_type"] == "normal"
    assert 4.5 <= item["gap_hours"] <= 5.5
    assert d["cancels"]["median_gap_hours"] == item["gap_hours"]

    # 90 天前的那次不算进「近 90 天」，但仍列在明细里
    db.get(Enrollment, enr.id).cancelled_at = now() - timedelta(days=120)
    db.commit()
    d = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert d["cancels"]["recent_count"] == 0 and d["cancels"]["total_count"] == 1


def test_搜索里的通配符只当普通字符(client, admin):
    """% 在 LIKE 里是通配符，直接拼进去会把全部人搜出来。"""
    all_rows = client.get("/api/staff/users", headers=admin).json()
    assert len(all_rows) > 1
    hit = client.get("/api/staff/users?q=%25", headers=admin).json()
    assert hit == [], "搜 % 应当一个人都不匹配"


def test_身份证号在库里是密文(client, db, take_student):
    """数据库备份被复制走、整库被拖，拿到的不能是原文。"""
    from sqlalchemy import text

    from backend.models import User

    sid = take_student()
    t = login(client, sid)
    card = "110101200512250016"
    r = client.put("/api/me/profile", headers=t,
                   json={"phone": "13900000000", "wechat": "kaoshi",
                         "campus": "中关村校区", "id_card": card})
    assert r.status_code == 200, r.text

    u = db.query(User).filter(User.student_id == sid).one()
    db.refresh(u)
    assert u.id_card == card          # 业务代码照常拿到明文

    raw = db.execute(text("SELECT id_card FROM users WHERE student_id = :s"),
                     {"s": sid}).scalar()
    assert raw and card not in raw, "库里存的仍是原文"
    assert len(raw) > len(card)
    # 出参永远只给脱敏值
    assert card not in r.text and r.json()["user"]["id_card_masked"].count("*") > 0
