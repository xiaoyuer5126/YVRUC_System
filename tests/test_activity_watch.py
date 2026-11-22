"""活动上新提醒：订阅每周空闲时段 / 关注的主单位 / 活动名关键词，新活动上广场时提醒一次。

时段命中、单位命中、关键词命中各发一条，同时命中也只发一条；都不命中、招募范围外、老师、补记录活动不发；
同一个活动只发一次（恢复报名、再次发布都不再发）；停在「待发布」的活动
以发起方点发布为准。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from backend import watch
from backend.integrations import msgcenter
from backend.models import Activity, ActivityWatch, now, uid

from conftest import (INITIATOR, approve, inbox, login, make_draft, student_row)
from test_record_activity import _create as create_record
from test_record_activity import _submit_and_approve

TITLE = "志愿活动上新提醒｜"
URL = "/api/me/activity-watch"


def _approved(db, open_group, **kw):
    """一个审批通过、停在「待发布」的活动（场次在若干天后）。"""
    act, g = open_group(5, **kw)
    act.status = "approved"
    act.reg_start = now() + timedelta(days=1)
    # 报名截止不能晚于最后一个场次开始，不然发布被拦
    act.reg_end = g.periods[0].start_time - timedelta(hours=1)
    act.organizer = f"测试单位 {uid('o')}"
    db.commit()
    return act, g


def _slots_of(g) -> set[str]:
    p = g.periods[0]
    return watch.period_slots(p.start_time, p.end_time)


def _publish(client, admin, act):
    r = client.post(f"/api/staff/activities/{act.id}/publish", headers=admin)
    assert r.status_code == 200, r.text


def _watch(client, sid, slots=(), organizers=()):
    r = client.put(URL, headers=login(client, sid),
                   json={"slots": list(slots), "organizers": list(organizers)})
    assert r.status_code == 200, r.text
    return r.json()


def _miss_slot(hit: set[str]) -> str:
    return next(k for k in watch.SLOT_KEYS if k not in hit)


# ======================= 分段与订阅接口 =======================

def test_时段按上午下午晚上切分():
    day = datetime(2026, 11, 23)          # 周一
    assert watch.period_slots(day.replace(hour=9), day.replace(hour=11)) == {"0-am"}
    assert watch.period_slots(day.replace(hour=11), day.replace(hour=13)) == {"0-am", "0-pm"}
    assert watch.period_slots(day.replace(hour=18), day.replace(hour=21)) == {"0-night"}
    # 结束在午夜整点不算沾到第二天；跨夜的算两天
    assert watch.period_slots(day.replace(hour=20), day + timedelta(days=1)) == {"0-night"}
    assert watch.period_slots(day.replace(hour=22), day + timedelta(days=1, hours=2)) \
        == {"0-night", "1-am"}
    # 凌晨出发算上午
    assert watch.period_slots(day.replace(hour=5), day.replace(hour=7)) == {"0-am"}
    # 时间灵活的按日子算，三段都沾
    assert watch.period_slots(day.replace(hour=9), day.replace(hour=10), True) \
        == {"0-am", "0-pm", "0-night"}
    assert watch.period_slots(day, day + timedelta(days=8)) == set(watch.SLOT_KEYS)


def test_订阅读写_排好顺序_校验格式(client, db, open_group, take_student):
    sid = take_student()
    h = login(client, sid)
    d = client.get(URL, headers=h).json()
    assert d["slots"] == [] and d["organizers"] == []
    assert [p["key"] for p in d["parts"]] == ["am", "pm", "night"]

    out = _watch(client, sid, slots=["6-night", "0-am", "0-am"],
                 organizers=[" 支教部 ", "支教部", "校青协支教部"])
    assert out["slots"] == ["0-am", "6-night"]
    assert out["organizers"] == ["支教部", "校青协支教部"]
    # 已有活动里没有的单位也照样列在选项里，不会一打开就丢掉
    assert {"支教部", "校青协支教部"} <= set(out["organizer_options"])
    assert client.get(URL, headers=h).json()["slots"] == ["0-am", "6-night"]

    r = client.put(URL, headers=h, json={"slots": ["7-am"], "organizers": []})
    assert r.status_code == 400
    r = client.put(URL, headers=h, json={"slots": ["0-noon"], "organizers": []})
    assert r.status_code == 400


def test_可选单位来自已公开的活动(client, db, open_group, take_student):
    act, _g = open_group(3)
    act.organizer = f"归纳单位 {uid('o')}"
    db.commit()
    d = client.get(URL, headers=login(client, take_student())).json()
    assert act.organizer in d["organizer_options"]


def test_老师不能订阅(client):
    h = login(client, INITIATOR)          # 李慕清是老师类型
    assert client.get(URL, headers=h).status_code == 403
    assert client.put(URL, headers=h, json={"slots": ["0-am"]}).status_code == 403


# ======================= 上广场时匹配 =======================

def test_时段命中发一条_点开进活动详情(client, db, open_group, take_student, admin):
    act, g = _approved(db, open_group)
    sid = take_student()
    _watch(client, sid, slots=sorted(_slots_of(g))[:1])
    _publish(client, admin, act)

    notes = inbox(db, sid, TITLE + act.title)
    assert len(notes) == 1
    n = notes[0]
    assert "空闲时段" in n.content and "关注" not in n.content
    assert act.organizer in n.content and "报名开始：" in n.content
    assert "报名开始前 3 分钟提醒" in n.content
    assert msgcenter.page_for(n.template_key, n.data) == f"/activity/{act.id}"


def test_单位命中发一条(client, db, open_group, take_student, admin):
    act, g = _approved(db, open_group)
    sid = take_student()
    _watch(client, sid, slots=[_miss_slot(_slots_of(g))], organizers=[act.organizer])
    _publish(client, admin, act)

    notes = inbox(db, sid, TITLE + act.title)
    assert len(notes) == 1
    assert f"关注的「{act.organizer}」" in notes[0].content
    assert "空闲时段" not in notes[0].content


def test_都不命中不发_没订阅不发(client, db, open_group, take_student, admin):
    act, g = _approved(db, open_group)
    miss = take_student()
    _watch(client, miss, slots=[_miss_slot(_slots_of(g))], organizers=["别的单位"])
    cleared = take_student()
    _watch(client, cleared, slots=sorted(_slots_of(g)), organizers=[act.organizer])
    _watch(client, cleared)               # 两样都清空 = 不订阅
    _publish(client, admin, act)
    assert inbox(db, miss, TITLE + act.title) == []
    assert inbox(db, cleared, TITLE + act.title) == []


def test_招募范围外不发(client, db, open_group, take_student, admin):
    act, g = _approved(db, open_group)
    act.recruit_campuses = ["苏州校区"]   # 用例学生在中关村校区
    db.commit()
    sid = take_student()
    _watch(client, sid, slots=sorted(_slots_of(g)), organizers=[act.organizer])
    _publish(client, admin, act)
    assert inbox(db, sid, TITLE + act.title) == []


def test_老师不发(client, db, open_group, admin):
    act, g = _approved(db, open_group)
    teacher = student_row(db, INITIATOR)
    row = db.get(ActivityWatch, teacher.id)
    if row is None:
        row = ActivityWatch(user_id=teacher.id)
        db.add(row)
    row.slots, row.organizers = list(watch.SLOT_KEYS), [act.organizer]
    db.commit()
    _publish(client, admin, act)
    assert inbox(db, INITIATOR, TITLE + act.title) == []


def test_同一活动只发一次_恢复报名与再次发布都不再发(client, db, open_group,
                                         take_student, admin):
    act, g = _approved(db, open_group)
    sid = take_student()
    _watch(client, sid, slots=sorted(_slots_of(g)), organizers=[act.organizer])
    _publish(client, admin, act)
    assert len(inbox(db, sid, TITLE + act.title)) == 1

    # 截止再恢复报名
    assert client.post(f"/api/staff/activities/{act.id}/close",
                       headers=admin).status_code == 200
    r = client.post(f"/api/staff/activities/{act.id}/reopen", headers=admin,
                    json={"reg_end": (now() + timedelta(days=3)).isoformat()})
    assert r.status_code == 200, r.text
    # 退回待发布后再点一次发布
    db.expire_all()
    db.get(Activity, act.id).status = "approved"
    db.commit()
    _publish(client, admin, act)
    assert len(inbox(db, sid, TITLE + act.title)) == 1


def test_审批通过即发布时提醒_停在待发布的以点发布为准(client, db, take_student, admin):
    sid = take_student()
    _watch(client, sid, organizers=["文学院青协"])   # make_draft 的主单位

    auto = make_draft(client, admin)
    approve(client, admin, auto)
    title = db.get(Activity, auto).title
    notes = inbox(db, sid, TITLE + title)
    assert len(notes) == 1 and "现在已可报名" in notes[0].content

    manual = make_draft(client, admin)
    approve(client, admin, manual, auto_publish=False)
    db.expire_all()
    title = db.get(Activity, manual).title
    assert inbox(db, sid, TITLE + title) == []
    r = client.post(f"/api/staff/activities/{manual}/publish", headers=admin)
    assert r.status_code == 200, r.text
    assert len(inbox(db, sid, TITLE + title)) == 1


def test_补记录活动不发(client, db, take_student, admin):
    sid = take_student()
    _watch(client, sid, slots=list(watch.SLOT_KEYS), organizers=["文学院青协"])
    aid = create_record(client, admin)
    assert _submit_and_approve(client, admin, aid)["status"] == "finished"
    assert inbox(db, sid, TITLE + db.get(Activity, aid).title) == []


# ======================= 活动名关键词 =======================

def _watch_kw(client, sid, keywords, **kw):
    r = client.put(URL, headers=login(client, sid),
                   json={"slots": list(kw.get("slots", ())),
                         "organizers": list(kw.get("organizers", ())),
                         "keywords": list(keywords)})
    assert r.status_code == 200, r.text
    return r.json()


def test_关键词读写_去空格去重_最多5个(client, take_student):
    sid = take_student()
    h = login(client, sid)
    d = client.get(URL, headers=h).json()
    assert d["keywords"] == [] and d["max_keywords"] == 5

    out = _watch_kw(client, sid, [" 支教 ", "支教", "Marathon", "marathon", "", "  "])
    assert out["keywords"] == ["支教", "Marathon"]
    assert client.get(URL, headers=h).json()["keywords"] == ["支教", "Marathon"]

    five = ["一", "二", "三", "四", "五"]
    assert _watch_kw(client, sid, five)["keywords"] == five
    # 去重后不超过 5 个的照收
    assert _watch_kw(client, sid, five + ["五 "])["keywords"] == five

    r = client.put(URL, headers=h, json={"keywords": five + ["六"]})
    assert r.status_code == 400 and "最多 5 个" in r.json()["detail"]
    r = client.put(URL, headers=h, json={"keywords": ["长" * 21]})
    assert r.status_code == 400
    assert _watch_kw(client, sid, ["长" * 20])["keywords"] == ["长" * 20]
    # 被拒的那次没改动已存的
    assert client.get(URL, headers=h).json()["keywords"] == ["长" * 20]


def test_活动名命中关键词发一条_注明关键词(client, db, open_group, take_student, admin):
    act, g = _approved(db, open_group)
    act.title = f"北京 Marathon 志愿服务 {uid('t')}"
    db.commit()
    sid = take_student()
    # 不分大小写；时段和单位都不命中
    _watch_kw(client, sid, ["无关词", "marathon"],
              slots=[_miss_slot(_slots_of(g))], organizers=["别的单位"])
    _publish(client, admin, act)

    notes = inbox(db, sid, TITLE + act.title)
    assert len(notes) == 1
    assert "关键词「marathon」" in notes[0].content
    assert "空闲时段" not in notes[0].content and "关注的" not in notes[0].content


def test_活动名不含关键词不发(client, db, open_group, take_student, admin):
    act, g = _approved(db, open_group)
    act.title = f"图书馆整理 {uid('t')}"
    db.commit()
    sid = take_student()
    _watch_kw(client, sid, ["支教", "马拉松"], slots=[_miss_slot(_slots_of(g))])
    _publish(client, admin, act)
    assert inbox(db, sid, TITLE + act.title) == []


def test_关键词与时段单位同时命中只发一条(client, db, open_group, take_student, admin):
    act, g = _approved(db, open_group)
    act.title = f"社区支教 {uid('t')}"
    db.commit()
    sid = take_student()
    _watch_kw(client, sid, ["支教"], slots=sorted(_slots_of(g)),
              organizers=[act.organizer])
    _publish(client, admin, act)

    notes = inbox(db, sid, TITLE + act.title)
    assert len(notes) == 1
    c = notes[0].content
    assert "空闲时段" in c and f"关注的「{act.organizer}」" in c and "关键词「支教」" in c


def test_关键词命中_招募范围外仍不发(client, db, open_group, take_student, admin):
    act, g = _approved(db, open_group)
    act.title = f"社区支教 {uid('t')}"
    act.recruit_campuses = ["苏州校区"]   # 用例学生在中关村校区
    db.commit()
    sid = take_student()
    _watch_kw(client, sid, ["支教"])
    _publish(client, admin, act)
    assert inbox(db, sid, TITLE + act.title) == []
