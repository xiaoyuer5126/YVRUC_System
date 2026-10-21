"""活动审批：提交 → 部长通过/驳回 → 发起方自己发布；转交与超时提醒。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import Activity, Department, User, now, uid
from conftest import POSITION_TEXT, login, upload_proposal

QINGXIE_HEAD = "2025004"        # 周砚，对外联络部部长
COUNCIL = "2025003"             # 张远航，会长团
CORE = "2025005"                # 林骁，对外联络部骨干


def _make_draft(client, admin, *, reg_start_in=timedelta(days=-1)) -> str:
    """一个结构完整、可以提交审批的草稿。reg_start_in 是报名开始距现在多久。"""
    r = client.post("/api/staff/activities", headers=admin,
                    json={"contact_group_qr": "x", "title": f"待审批活动 {uid('t')}", "organizer": "文学院青协",
                          "intro": "x", "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() + reg_start_in).isoformat(),
                          "reg_end": (now() + timedelta(days=5)).isoformat()})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    r = client.post(f"/api/staff/activities/{aid}/shifts", headers=admin,
                    json={"name": "上午场", "mode": "pick",
                          "periods": [{"start_time": (now() + timedelta(days=9)).isoformat(),
                          "end_time": (now() + timedelta(days=9, hours=3)).isoformat()}]})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/shifts/{r.json()['id']}/positions", headers=admin,
                    json={"name": "引导", "quota": 3, **POSITION_TEXT})
    assert r.status_code == 200, r.text
    # 这里的用例大多交给对外联络部（种子里唯一有部长的部门），它要立项书
    upload_proposal(client, admin, aid)
    return aid


@pytest.fixture
def draft(client, admin):
    return _make_draft(client, admin)


@pytest.fixture
def liaison_head_restored(client, admin, db):
    """用例会把对外联络部部长调走；结束时（无论成败）放回去并确认放回去了。

    部长是全库共享的状态，放不回去的话后面所有走审批的用例都会莫名失败。
    """
    yield
    r = client.patch(f"/api/staff/departments/{_liaison(db).id}", headers=admin,
                     json={"head_student_id": QINGXIE_HEAD})
    assert r.status_code == 200, r.text
    rows = client.get("/api/staff/departments", headers=admin).json()
    assert next(d for d in rows if d["name"] == "对外联络部")["head_name"] == "周砚"


def _events(client, headers, activity_id) -> list[dict]:
    r = client.get(f"/api/staff/activities/{activity_id}/approval", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["events"]


def _my_notes(client, headers, activity_id, title_prefix) -> list[dict]:
    """某人收件箱里关于这场活动、标题以 title_prefix 开头的通知。"""
    r = client.get("/api/me/notifications", headers=headers)
    assert r.status_code == 200, r.text
    return [n for n in r.json()["items"]
            if n["data"].get("activity_id") == activity_id
            and n["title"].startswith(title_prefix)]


def _liaison(db):
    return db.query(Department).filter(Department.name == "对外联络部").one()


def test_草稿不能绕过审批直接发布(client, admin, draft):
    r = client.post(f"/api/staff/activities/{draft}/publish", headers=admin)
    assert r.status_code == 409 and "先提交校青协审批" in r.json()["detail"]


def test_提交后由部长通过即发布(client, admin, db):
    # 报名还没开始的活动：通过即发布不该等到报名开始那一刻
    draft = _make_draft(client, admin, reg_start_in=timedelta(days=2))
    dept = _liaison(db)
    r = client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                    json={"department_id": dept.id, "note": "校庆相关"})
    assert r.status_code == 200 and r.json()["assignee"] == "周砚"
    db.expire_all()
    assert db.get(Activity, draft).status == "pending"

    # 待审批列表里，部长看得到自己那条
    head = login(client, QINGXIE_HEAD)
    ids = [a["id"] for a in client.get("/api/staff/approvals", headers=head).json()]
    assert draft in ids

    r = client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                    json={"approve": True, "comment": "同意"})
    assert r.status_code == 200 and r.json()["status"] == "published"
    # 审批结果发给活动管理者，点开进活动管理页的「审核」页签
    notes = _my_notes(client, admin, draft, "审批通过")
    assert len(notes) == 1 and notes[0]["page"] == f"/manage/{draft}?tab=review"
    # 报名开始时间没到也照样发布，广场上显示倒计时
    d = client.get(f"/api/activities/{draft}", headers=admin).json()
    assert d["status"] == "published"
    assert d["opens_in"] > 0


def test_关掉通过即发布就停在待发布(client, admin, db, draft):
    """想踩着某个时刻自己放出去的，通过后活动还在发起方手里。"""
    head = login(client, QINGXIE_HEAD)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": _liaison(db).id, "auto_publish": False})
    r = client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                    json={"approve": True})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    db.expire_all()
    assert db.get(Activity, draft).status == "approved"
    assert client.post(f"/api/staff/activities/{draft}/publish",
                       headers=admin).status_code == 200
    db.expire_all()
    assert db.get(Activity, draft).status == "published"


def test_审批期间把安排改坏了就通不过(client, admin, db, draft):
    """提交之后发起方还能改；通过的那一刻活动就上线，不能带着窟窿出去。"""
    head = login(client, QINGXIE_HEAD)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": _liaison(db).id})
    act = db.get(Activity, draft)
    act.contact_group_qr = ""
    db.commit()
    r = client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                    json={"approve": True})
    assert r.status_code == 400 and "联络群" in r.json()["detail"]
    db.expire_all()
    assert db.get(Activity, draft).status == "pending"


def test_驳回必须写理由并退回草稿(client, admin, db, draft):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    head = login(client, QINGXIE_HEAD)

    r = client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                    json={"approve": False, "comment": ""})
    assert r.status_code == 400 and "必须写明理由" in r.json()["detail"]

    r = client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                    json={"approve": False, "comment": "岗位要求写得太笼统"})
    assert r.status_code == 200
    db.expire_all()
    act = db.get(Activity, draft)
    assert act.status == "draft" and "笼统" in act.approval_comment

    # 改完可以重新提交（驳回时立项书作废了，要传一份新的）
    upload_proposal(client, admin, draft)
    assert client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                       json={"department_id": dept.id}).status_code == 200


def test_不是审批人就拍不了板(client, admin, db, draft, take_student):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    other = login(client, take_student())
    r = client.post(f"/api/staff/activities/{draft}/decide", headers=other,
                    json={"approve": True})
    assert r.status_code == 403


def test_会长团可以接管任何一条(client, admin, db, draft):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    council = login(client, COUNCIL)
    assert draft in [a["id"] for a in
                     client.get("/api/staff/approvals", headers=council).json()]
    r = client.post(f"/api/staff/activities/{draft}/decide", headers=council,
                    json={"approve": True, "comment": "会长团直接通过"})
    assert r.status_code == 200 and r.json()["status"] == "published"
    last = _events(client, admin, draft)[-1]
    assert last["kind"] == "approve" and last["actor"] == "张远航"


def test_转交要写理由且换审批人(client, admin, db, draft):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    head = login(client, QINGXIE_HEAD)

    r = client.post(f"/api/staff/activities/{draft}/transfer-approval",
                    headers=head, json={"student_id": COUNCIL, "reason": ""})
    assert r.status_code == 400 and "转交理由" in r.json()["detail"]

    r = client.post(f"/api/staff/activities/{draft}/transfer-approval",
                    headers=head,
                    json={"student_id": COUNCIL, "reason": "该活动跨部门，请会长定"})
    assert r.status_code == 200 and r.json()["assignee"] == "张远航"

    # 转出之后原审批人就拍不了了
    assert client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                       json={"approve": True}).status_code == 403

    events = client.get(f"/api/staff/activities/{draft}/approval",
                        headers=admin).json()["events"]
    assert [e["kind"] for e in events] == ["submit", "transfer"]
    assert "跨部门" in events[-1]["comment"]


def test_没有部长的部门不能被选(client, admin, db, draft):
    empty = (db.query(Department)
             .filter(Department.name == "支教项目部").one())
    r = client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                    json={"department_id": empty.id})
    assert r.status_code == 400 and "还没有指定部长" in r.json()["detail"]


def test_部长自审要在流水里标明本人(client, db, draft):
    """部长自己办的活动归口自己部门、由自己审：允许，但留痕写清是本人。"""
    from backend.models import ActivityManager

    head_user = db.query(User).filter(User.student_id == QINGXIE_HEAD).one()
    db.add(ActivityManager(id=uid("mgr"), activity_id=draft,
                           user_id=head_user.id, added_by=head_user.id))
    db.commit()
    head = login(client, QINGXIE_HEAD)
    r = client.post(f"/api/staff/activities/{draft}/submit", headers=head,
                    json={"department_id": _liaison(db).id})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                    json={"approve": True, "comment": "自己批"})
    assert r.status_code == 200, r.text
    approves = [e for e in _events(client, head, draft) if e["kind"] == "approve"]
    assert len(approves) == 1
    assert approves[0]["comment"].startswith("【本人审批】")


def test_青协建的活动自动归口自己部门(client, db, draft):
    """提交时不选部门也能交：归口已经按创建者所在部门预填。"""
    head = login(client, QINGXIE_HEAD)
    r = client.post("/api/staff/activities", headers=head,
                    json={"contact_group_qr": "x", "title": f"归口预填 {uid('t')}", "organizer": "校青协",
                          "intro": "x", "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(days=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=5)).isoformat()})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    db.expire_all()
    assert db.get(Activity, aid).approval_dept_id == _liaison(db).id
    # 部长在活动列表里直接看得到本部门的草稿
    assert aid in [a["id"] for a in
                   client.get("/api/staff/activities", headers=head).json()["items"]]


def test_审批超时只催人不放行(client, admin, db, draft):
    from backend import scheduled

    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    act = db.get(Activity, draft)
    act.submitted_at = now() - timedelta(hours=48)
    db.commit()

    # 返回值是全库催了几条，别的用例也可能留下待审批的活动，所以只按本活动的通知数
    head = login(client, QINGXIE_HEAD)
    assert scheduled.remind_stale_approvals(db) >= 1
    assert client.get(f"/api/staff/activities/{draft}/approval",
                      headers=admin).json()["status"] == "pending"   # 绝不自动通过
    assert len(_my_notes(client, head, draft, "审批已等待")) == 1

    # 催过一次就不再催
    scheduled.remind_stale_approvals(db)
    assert len(_my_notes(client, head, draft, "审批已等待")) == 1


# ======================= 审批人看得到活动 =======================

def test_审批人能看未公开活动的完整安排(client, admin, db, draft, take_student):
    """看不到岗位与培训内容就没法批。审批页上那个「查看完整安排」要真能点开。

    归口部门的部长本来就按管理者视角看本部门活动；这里验的是被**转交**到手、
    又不是这个活动管理者的人——比如本部门的骨干。
    """
    head, core = login(client, QINGXIE_HEAD), login(client, CORE)
    assert client.get(f"/api/activities/{draft}", headers=core).status_code == 404

    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": _liaison(db).id})
    assert client.post(f"/api/staff/activities/{draft}/transfer-approval",
                       headers=head, json={"student_id": CORE, "reason": "请骨干先看"}
                       ).status_code == 200
    d = client.get(f"/api/activities/{draft}", headers=core)
    assert d.status_code == 200, d.text
    d = d.json()
    assert d["review_preview"] is True and d["status_label"] == "待审批"
    # 培训内容是判断活动办不办得成的依据，按管理者视角给
    assert d["shifts"][0]["positions"][0]["training_visible"] is True

    # 无关的人照样看不见
    other = login(client, take_student())
    assert client.get(f"/api/activities/{draft}", headers=other).status_code == 404


def test_批过之后仍能回看自己批的那条(client, admin, db, draft):
    """通过到发起方点发布之间活动还不公开，审批人要能核对自己批了什么。

    用骨干而不是部长来批：部长按归口部门本来就看得到，验不出这条规则。
    """
    head, core = login(client, QINGXIE_HEAD), login(client, CORE)
    assert client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                       json={"department_id": _liaison(db).id,
                             "auto_publish": False}).status_code == 200
    assert client.post(f"/api/staff/activities/{draft}/transfer-approval",
                       headers=head, json={"student_id": CORE, "reason": "请骨干定"}
                       ).status_code == 200
    r = client.post(f"/api/staff/activities/{draft}/decide", headers=core,
                    json={"approve": True})
    assert r.status_code == 200 and r.json()["status"] == "approved"

    d = client.get(f"/api/activities/{draft}", headers=core)
    assert d.status_code == 200, d.text
    assert d.json()["review_preview"] is True


def test_活动一旦公开审批人就退回普通视角(client, admin, db, draft, take_student):
    """公开之后他和别人看到的该是同一页——那时活动里已经有报名的人了。"""
    head, core = login(client, QINGXIE_HEAD), login(client, CORE)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": _liaison(db).id})
    client.post(f"/api/staff/activities/{draft}/transfer-approval", headers=head,
                json={"student_id": CORE, "reason": "请骨干看"})
    client.post(f"/api/staff/activities/{draft}/decide", headers=core,
                json={"approve": True})

    d = client.get(f"/api/activities/{draft}", headers=core).json()
    assert d["review_preview"] is False
    assert d["shifts"][0]["positions"][0]["training_visible"] is False


def test_转交审批就是改归口(client, admin, db, draft, take_student):
    """转给哪个部门，活动就归那个部门；原部门部长的列表里随之消失。"""
    head = login(client, QINGXIE_HEAD)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": _liaison(db).id})
    other = db.query(Department).filter(Department.name == "环保项目部").one()
    # 给环保项目部临时立一位部长，转交才有人接
    new_head = db.query(User).filter(User.student_id == take_student()).one()
    assert client.put(f"/api/staff/roles/{new_head.id}", headers=admin,
                      json={"roles": ["student", "qingxie"], "qingxie_post": "head",
                            "department_id": other.id}).status_code == 200
    try:
        assert client.post(f"/api/staff/activities/{draft}/transfer-approval",
                           headers=head, json={"department_id": other.id,
                                               "reason": "环保类活动归他们"}
                           ).status_code == 200
        db.expire_all()
        act = db.get(Activity, draft)
        assert act.approval_dept_id == other.id
        assert draft not in [a["id"] for a in
                             client.get("/api/staff/activities", headers=head).json()["items"]]
        # 转出对外联络部就是内部活动：立项书删掉，并告诉管理者
        from backend.models import Attachment
        assert not db.query(Attachment).filter(Attachment.owner_type == "proposal",
                                               Attachment.owner_id == draft).count()
        from conftest import ADMIN, inbox
        note = inbox(db, ADMIN, f"审批已转交｜{act.title}")[0]
        assert "立项书已删除" in note.content
    finally:
        # 复原：撤掉这位临时部长，别让环保项目部从此「有部长」影响别的用例
        r = client.put(f"/api/staff/roles/{new_head.id}", headers=admin,
                       json={"roles": ["student"]})
        assert r.status_code == 200, r.text
        rows = client.get("/api/staff/departments", headers=admin).json()
        assert next(d for d in rows if d["name"] == "环保项目部")["has_head"] is False


def test_只有管理员能指定部长(client, admin, db, liaison_head_restored):
    head = login(client, QINGXIE_HEAD)
    dept = db.query(Department).filter(Department.name == "环保项目部").one()
    assert client.patch(f"/api/staff/departments/{dept.id}", headers=head,
                        json={"head_student_id": QINGXIE_HEAD}).status_code == 403

    r = client.patch(f"/api/staff/departments/{dept.id}", headers=admin,
                     json={"head_student_id": QINGXIE_HEAD})
    assert r.status_code == 200
    rows = client.get("/api/staff/departments", headers=admin).json()
    assert next(d for d in rows if d["name"] == "环保项目部")["head_name"] == "周砚"
    # 换部门后原部门的部长位就空了
    assert next(d for d in rows if d["name"] == "对外联络部")["head_name"] == ""


def test_提交说明要出现在审批页上(client, admin, db, draft):
    """审批人就是在那一屏点通过/驳回的，提交时写的话必须在那里看得到。

    只存进流水、发进通知而审批列表接口不返回的话，等于让人在看不到提交说明
    的情况下拍板。
    """
    NOTE = "下午场人力偏紧，麻烦重点看一下"
    dept = _liaison(db)
    assert client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                       json={"department_id": dept.id,
                             "note": NOTE}).status_code == 200

    row = next(x for x in client.get("/api/staff/approvals",
                                     headers=admin).json() if x["id"] == draft)
    assert row["submit_note"] == NOTE


def test_没写说明就给空串而不是缺字段(client, admin, db, draft):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    row = next(x for x in client.get("/api/staff/approvals",
                                     headers=admin).json() if x["id"] == draft)
    assert row["submit_note"] == ""


def test_待审批的通知指向审批页_不要签收(client, admin, db, draft):
    """审批人在活动详情页看不到通过/驳回，消息点开要进审批页。
    办没办看审批状态就知道，不另要一次签收。"""
    dept = _liaison(db)
    r = client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                    json={"department_id": dept.id})
    assert r.status_code == 200, r.text
    notes = _my_notes(client, login(client, QINGXIE_HEAD), draft, "志愿活动待审批")
    assert len(notes) == 1 and notes[0]["page"] == "/approvals"
    assert notes[0]["need_ack"] is False


def test_部门可以新增和删除(client, admin, db):
    """换届之外还有增设与撤销：新部门建出来能选，空部门删掉就没了。"""
    before = [d["name"] for d in
              client.get("/api/staff/departments", headers=admin).json()]
    r = client.post("/api/staff/departments", headers=admin,
                    json={"name": "测试新部门", "note": "临时"})
    assert r.status_code == 200, r.text
    dept_id = r.json()["id"]
    rows = client.get("/api/staff/departments", headers=admin).json()
    assert rows[-1]["name"] == "测试新部门"       # 排在最后
    assert rows[-1]["has_head"] is False

    assert client.post("/api/staff/departments", headers=admin,
                       json={"name": "测试新部门"}).status_code == 409

    assert client.delete(f"/api/staff/departments/{dept_id}",
                         headers=admin).status_code == 200
    names = [d["name"] for d in
             client.get("/api/staff/departments", headers=admin).json()]
    # 回到建之前的样子：新部门没了，也没有被默认部门表重新灌回什么
    assert names == before


def test_名下还有人或活动的部门不能删(client, admin, db, draft,
                             liaison_head_restored):
    dept = _liaison(db)
    r = client.delete(f"/api/staff/departments/{dept.id}", headers=admin)
    assert r.status_code == 409 and "还有" in r.json()["detail"]

    empty = client.post("/api/staff/departments", headers=admin,
                        json={"name": f"测试挂活动部门 {uid('t')}"}).json()["id"]
    assert client.patch(f"/api/staff/departments/{empty}", headers=admin,
                        json={"head_student_id": QINGXIE_HEAD}).status_code == 200
    assert client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                       json={"department_id": empty}).status_code == 200
    # 部长撤走，剩下的只有那场活动的归口
    assert client.patch(f"/api/staff/departments/{_liaison(db).id}", headers=admin,
                        json={"head_student_id": QINGXIE_HEAD}).status_code == 200
    r = client.delete(f"/api/staff/departments/{empty}", headers=admin)
    assert r.status_code == 409 and "归口着 1 场活动" in r.json()["detail"]


def test_审批人打开活动管理页能直接去审批(client, initiator, liaison):
    """审批详情的 can_decide 决定活动管理页右上角是「去审批」还是「待 xx 审批」。"""
    from conftest import LIAISON_HEAD, make_draft
    aid = make_draft(client, initiator)
    r = client.post(f"/api/staff/activities/{aid}/submit", headers=initiator,
                    json={"department_id": liaison.id})
    assert r.status_code == 200, r.text
    url = f"/api/staff/activities/{aid}/approval"
    assert client.get(url, headers=initiator).json()["can_decide"] is False
    head = login(client, LIAISON_HEAD)
    assert client.get(url, headers=head).json()["can_decide"] is True
    assert aid in [a["id"] for a in client.get("/api/staff/approvals",
                                               headers=head).json()]


def test_多部长时等谁审批列全部部长_移除一位只动他(client, admin, db, draft,
                                       take_student):
    """审批挂在部长名下时本部门任一部长都能拍，「等谁审批」要列出全部在岗部长；
    在部门编辑里移除其中一位，他降为本部门成员，别的部长和部门说明都不动。"""
    from conftest import student_row
    dept = _liaison(db)
    note_before = dept.note
    u = student_row(db, take_student())
    try:
        assert client.put(f"/api/staff/roles/{u.id}", headers=admin,
                          json={"roles": ["student", "qingxie"], "qingxie_post": "head",
                                "department_id": dept.id}).status_code == 200
        r = client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                        json={"department_id": dept.id})
        assert r.status_code == 200, r.text
        both = f"周砚、{u.name}"
        detail = client.get(f"/api/staff/activities/{draft}/approval",
                            headers=admin).json()
        assert detail["assignee"] == both
        row = next(x for x in client.get("/api/staff/approvals", headers=admin).json()
                   if x["id"] == draft)
        assert row["assignee"] == both

        # 部门编辑里移除新加的那位：只传学号，不带说明
        r = client.patch(f"/api/staff/departments/{dept.id}", headers=admin,
                         json={"remove_head_student_id": u.student_id})
        assert r.status_code == 200, r.text
        db.expire_all()
        assert db.get(User, u.id).qingxie_post == "member"
        assert db.get(User, u.id).department_id == dept.id
        assert _liaison(db).note == note_before
        heads = next(d for d in client.get("/api/staff/departments", headers=admin).json()
                     if d["id"] == dept.id)["heads"]
        assert [h["name"] for h in heads] == ["周砚"]
        assert client.get(f"/api/staff/activities/{draft}/approval",
                          headers=admin).json()["assignee"] == "周砚"
        # 不是这个部门的部长：报 404，不误动别人
        r = client.patch(f"/api/staff/departments/{dept.id}", headers=admin,
                         json={"remove_head_student_id": u.student_id})
        assert r.status_code == 404
    finally:
        assert client.put(f"/api/staff/roles/{u.id}", headers=admin,
                          json={"roles": ["student"]}).status_code == 200


def test_转给个人的审批只显示那个人(client, admin, db, draft):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    act = db.get(Activity, draft)
    core = db.query(User).filter(User.student_id == CORE).one()
    from backend.routers.approvals import assignee_text
    act.approval_assignee_id = core.id
    assert assignee_text(db, act) == core.name
    db.rollback()
