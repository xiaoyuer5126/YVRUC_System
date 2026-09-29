"""志愿者档案：只记不罚。

青协能看到一个人参与过的活动、违规记录、临期退选次数；平台不据此拦任何人。
"""
from __future__ import annotations

from datetime import timedelta

from backend import config
from backend.models import Enrollment, User, now
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


def test_临期退选只扣留名额不处罚志愿者(client, admin, db, open_group, take_student):
    """对志愿者：只是那个名额被扣留，不提分数、不拦报名。
    对青协：内部提示分按临期退选的权重掉一截，供排人时参考。"""
    act, sid, body = _late_cancel(client, db, open_group, take_student)
    assert body["effect"] == "held"
    assert "分" not in body["message"]
    u = student_row(db, sid)
    d = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    # 新建的学生没有别的记录，扣的就只有这一次
    assert d["score"]["score"] == config.SCORE_BASE - config.LATE_CANCEL_WEIGHT
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


def test_同一活动重复登记先回409带已有记录确认后照登(client, admin, db,
                                             enrolled_activity):
    """两个管理者各登一遍是常事：第二条先把已有的摆出来问一句，确认了才记。"""
    act, _shift, u = enrolled_activity
    url = "/api/staff/violations"
    first = {"user_id": u.id, "activity_id": act.id,
             "kind": "late", "detail": "迟到 20 分钟"}
    assert client.post(url, headers=admin, json=first).status_code == 200
    listing = f"/api/staff/activities/{act.id}/volunteers/{u.id}/violations"
    items = client.get(listing, headers=admin).json()["items"]
    assert [v["kind"] for v in items] == ["late"]

    again = {**first, "kind": "passive", "detail": "全程玩手机"}
    r = client.post(url, headers=admin, json=again)
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "VIOLATION_DUPLICATE"
    assert len(d["items"]) == 1
    v = d["items"][0]
    assert v["kind_label"] == "迟到早退" and v["detail"] == "迟到 20 分钟"
    assert v["created_at"] and v["operator_name"]
    # 没记进去
    assert len(client.get(listing, headers=admin).json()["items"]) == 1

    r = client.post(url, headers=admin, json={**again, "confirm_duplicate": True})
    assert r.status_code == 200, r.text
    kinds = [v["kind"] for v in client.get(listing, headers=admin).json()["items"]]
    assert sorted(kinds) == ["late", "passive"]


def test_撤销违规登记(client, admin, db, enrolled_activity):
    act, _shift, u = enrolled_activity
    vid = client.post("/api/staff/violations", headers=admin,
                      json={"user_id": u.id, "activity_id": act.id,
                            "kind": "other", "detail": "登记错了"}).json()["id"]
    d = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert vid in [v["id"] for v in d["violations"]]
    assert client.delete(f"/api/staff/violations/{vid}", headers=admin).status_code == 200
    d = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert vid not in [v["id"] for v in d["violations"]]


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
    W = config.VIOLATION_WEIGHTS
    base = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert base["score"]["score"] == config.SCORE_BASE
    assert base["score"]["score_alert"] is False

    ids = []
    for kind in ("contact", "leave", "late", "passive"):
        r = client.post("/api/staff/violations", headers=admin,
                        json={"user_id": u.id, "activity_id": _act.id,
                              "kind": kind, "detail": "演示",
                              "confirm_duplicate": True})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    d = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    four = config.SCORE_BASE - sum(W[k] for k in ("contact", "leave", "late", "passive"))
    assert d["score"]["score"] == four
    # 下面两处「挂/不挂感叹号」依赖分数落在阈值两侧；权重改了这里先报出来
    assert four < config.SCORE_ALERT_BELOW <= four + W["contact"]
    assert d["score"]["score_alert"] is True

    # 名单上这个人要挂感叹号
    roster = client.get(f"/api/staff/shifts/{_shift.id}/roster", headers=admin).json()
    me = next(r for r in roster["enrolled"] if r["user"]["id"] == u.id)
    assert me["user"]["score_alert"] is True and me["user"]["score"] == four

    assert client.delete(f"/api/staff/violations/{ids[0]}",
                         headers=admin).status_code == 200
    d = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert d["score"]["score"] == four + W["contact"]
    assert d["score"]["score_alert"] is False


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
