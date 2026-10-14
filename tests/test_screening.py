"""报名后筛选：填表申请、意向场次、录取与未录取；联络群二维码只弹一次。"""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import unquote

from backend.models import (Activity, Application, Enrollment, Notification,
                            User, now)
from conftest import dl_url, login, student_row


def _create_screened(client, admin, db, *, status="published", shifts=2):
    r = client.post("/api/staff/activities", headers=admin,
                    json={"title": f"筛选活动 {now():%H%M%S%f}", "organizer": "校青协",
                          "recruit_mode": "screened", "intro": "x",
                          "location": "东门", "contact_group_qr": "data:qr",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(days=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=3)).isoformat()})
    assert r.status_code == 200, r.text
    act_id = r.json()["id"]
    ids = []
    for i in range(shifts):
        start = now() + timedelta(days=5, hours=i * 5)
        r = client.post(f"/api/staff/activities/{act_id}/shifts", headers=admin,
                        json={"name": f"第{i + 1}场", "mode": "assign", "capacity": 10,
                              "start_time": start.isoformat(),
                              "end_time": (start + timedelta(hours=3)).isoformat()})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    r = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                   json={"questions": [
                       {"kind": "single", "title": "参加过吗", "options": ["是", "否"]},
                       {"kind": "multi", "title": "能做什么",
                        "options": ["引导", "搬运", "登记"]},
                       {"kind": "text", "title": "介绍自己", "required": False},
                       {"kind": "file", "title": "证明材料", "required": False},
                   ]})
    assert r.status_code == 200, r.text
    questions = r.json()["questions"]
    db.get(Activity, act_id).status = status
    db.commit()
    return act_id, ids, questions


def _answers(questions, **override):
    by_title = {q["title"]: q["id"] for q in questions}
    base = {by_title["参加过吗"]: "是", by_title["能做什么"]: ["引导", "登记"],
            by_title["介绍自己"]: "我很热心"}
    for title, v in override.items():
        base[by_title[title]] = v
    return base


def _holders(db, shift_ids):
    """「截止后统一分配」的场次只有一个「待分配」占位岗位，勾它等于勾这一场。"""
    from backend.models import Shift
    return [db.get(Shift, g).placeholder().id for g in shift_ids]


def _pick_shift(client, admin, act_id, name="选岗场", *, days=6, positions=("引导", "登记")):
    """给筛选活动加一个「报名即选岗位」的场次，返回 (场次 id, 岗位 id 列表)。"""
    start = now() + timedelta(days=days)
    r = client.post(f"/api/staff/activities/{act_id}/shifts", headers=admin,
                    json={"name": name, "mode": "pick",
                          "start_time": start.isoformat(),
                          "end_time": (start + timedelta(hours=3)).isoformat()})
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    pids = []
    for p in positions:
        rp = client.post(f"/api/staff/shifts/{gid}/positions", headers=admin,
                         json={"name": p, "quota": 2, "location": "东门",
                               "service_task": "现场服务",
                               "training_note": "到岗讲解"})
        assert rp.status_code == 200, rp.text
        pids.append(rp.json()["id"])
    return gid, pids


def _placed(client, admin, act_id) -> dict[str, list[str]]:
    """管理者的申请名单里，每份申请录到了哪些岗位（申请 id → 岗位名）。"""
    lst = client.get(f"/api/staff/activities/{act_id}/applications", headers=admin).json()
    return {a["id"]: [w["position_name"] for w in a["wanted"]
                      if w["status"] == "enrolled"]
            for a in lst["applications"]}


def test_筛选活动的两类场次都能用(client, admin, db):
    """统一分配的场次只勾场次；报名即选岗位的场次连岗位一起勾。"""
    act_id, ids, _q = _create_screened(client, admin, db, status="draft")
    gid, _pids = _pick_shift(client, admin, act_id)
    d = client.get(f"/api/activities/{act_id}", headers=admin).json()
    modes = {g["name"]: g["mode"] for g in d["shifts"]}
    assert modes["第1场"] == "assign" and modes["选岗场"] == "pick"
    assert any(p["is_placeholder"]
               for g in d["shifts"] if g["id"] == ids[0] for p in g["positions"])


def test_报名即选岗位的场次要连岗位一起勾(client, admin, db, take_student):
    act_id, _ids, q = _create_screened(client, admin, db, status="draft", shifts=0)
    gid, pids = _pick_shift(client, admin, act_id)
    db.get(Activity, act_id).status = "published"
    db.commit()
    t = login(client, take_student())

    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": [], "answers": _answers(q)})
    assert r.status_code == 400 and "意向岗位" in r.json()["detail"]

    # 同一场次可以勾好几个岗位：意向不占名额，录取时管理者挑一个
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": [pids[1], pids[0]],
                          "answers": _answers(q)})
    assert r.status_code == 200, r.text
    mine = client.get(f"/api/activities/{act_id}", headers=t).json()["application"]["mine"]
    assert mine["wanted"][0]["position_name"] == "登记、引导"
    assert mine["wanted_positions"] == [pids[1], pids[0]]

    # 录取后就在他勾的那个岗位上，不用再分配
    lst = client.get(f"/api/staff/activities/{act_id}/applications", headers=admin).json()
    assert lst["shifts"][0]["mode"] == "pick"
    assert [p["name"] for p in lst["shifts"][0]["positions"]] == ["引导", "登记"]
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": [lst["applications"][0]["id"]],
                          "accept": True})
    assert r.status_code == 200, r.text
    assert _placed(client, admin, act_id) == {lst["applications"][0]["id"]: ["登记"]}


def test_录取时逐人指定岗位(client, admin, db, take_student):
    """一批人一起录，去向各不相同：批量下拉只是默认值，行内选的说了算。

    两人的意向都跟最终去向不同：第一人被整批指定改到别的岗位（整批 > 意向），
    第二人被行内指定再改一次（逐人 > 整批）。
    """
    act_id, _ids, q = _create_screened(client, admin, db, status="draft", shifts=0)
    gid, pids = _pick_shift(client, admin, act_id)       # 引导、登记
    db.get(Activity, act_id).status = "published"
    db.commit()
    for wish in (pids[1], pids[0]):
        t = login(client, take_student())
        r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                        json={"position_ids": [wish], "answers": _answers(q)})
        assert r.status_code == 200, r.text
    apps = client.get(f"/api/staff/activities/{act_id}/applications",
                      headers=admin).json()["applications"]
    first, second = apps[0]["id"], apps[1]["id"]
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": [first, second], "accept": True,
                          "targets": [{"shift_id": gid, "position_id": pids[0]}],
                          "picks": {second: [{"shift_id": gid,
                                              "position_id": pids[1]}]}})
    assert r.status_code == 200, r.text
    assert _placed(client, admin, act_id) == {first: ["引导"], second: ["登记"]}


def test_录取到不满足条件的岗位会留痕(client, admin, db, take_student):
    """筛选是人的判断，不拦；但名单和留痕上要看得出来。"""
    from backend.models import AuditLog, Position

    act_id, _ids, q = _create_screened(client, admin, db, status="draft", shifts=0)
    gid, pids = _pick_shift(client, admin, act_id)
    db.get(Position, pids[1]).requirement_tags = {"学院": ["不存在的学院"]}
    db.get(Activity, act_id).status = "published"
    db.commit()
    t = login(client, take_student())
    client.post(f"/api/activities/{act_id}/apply", headers=t,
                json={"position_ids": [pids[0]],
                      "answers": _answers(q)})
    app_id = client.get(f"/api/staff/activities/{act_id}/applications",
                        headers=admin).json()["applications"][0]["id"]
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": [app_id], "accept": True,
                          "picks": {app_id: [{"shift_id": gid,
                                              "position_id": pids[1]}]}})
    assert r.status_code == 200, r.text
    row = (db.query(Enrollment)
           .filter(Enrollment.activity_id == act_id,
                   Enrollment.status == "enrolled").one())
    assert row.position_id == pids[1] and row.assign_forced is True
    log = (db.query(AuditLog)
           .filter(AuditLog.activity_id == act_id, AuditLog.action == "assign")
           .order_by(AuditLog.created_at.desc()).first())
    assert "不满足" in log.summary


def test_题目按显示条件出现(client, admin, db, take_student):
    """挂条件的题只在选了指定选项时出现：不出现就不填、也不存答案。"""
    act_id, ids, _q = _create_screened(client, admin, db, status="draft")
    base = client.get(f"/api/staff/activities/{act_id}/form", headers=admin).json()
    yes_no = base["questions"][0]        # 单选：参加过吗
    r = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                   json={"questions": [
                       {"id": yes_no["id"], "kind": "single", "title": "参加过吗",
                        "options": ["是", "否"]},
                       {"kind": "text", "title": "上次做了什么",
                        "show_if": {"question_id": yes_no["id"], "values": ["是"]}},
                   ]})
    assert r.status_code == 200, r.text
    q_all = r.json()["questions"]
    follow = q_all[1]["id"]
    assert q_all[1]["show_if"]["values"] == ["是"]

    # 条件只能挂在前面的选择题上
    bad = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                     json={"questions": [
                         {"kind": "text", "title": "先问细节",
                          "show_if": {"question_id": yes_no["id"], "values": ["是"]}},
                         {"id": yes_no["id"], "kind": "single", "title": "参加过吗",
                          "options": ["是", "否"]}]})
    assert bad.status_code == 400 and "前面" in bad.json()["detail"]

    db.get(Activity, act_id).status = "published"
    db.commit()
    # 选「否」：跟进题不出现，不填也能交，答案里不留它
    t = login(client, take_student())
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": _holders(db, ids[:1]),
                          "answers": {yes_no["id"]: "否", follow: "不该存下来"}})
    assert r.status_code == 200, r.text
    mine = client.get(f"/api/activities/{act_id}", headers=t).json()["application"]["mine"]
    assert follow not in mine["answers"]

    # 选「是」：跟进题是必填，不填就交不了
    t2 = login(client, take_student())
    r = client.post(f"/api/activities/{act_id}/apply", headers=t2,
                    json={"position_ids": _holders(db, ids[:1]), "answers": {yes_no["id"]: "是"}})
    assert r.status_code == 400 and "上次做了什么" in r.json()["detail"]
    r = client.post(f"/api/activities/{act_id}/apply", headers=t2,
                    json={"position_ids": _holders(db, ids[:1]),
                          "answers": {yes_no["id"]: "是", follow: "带过路"}})
    assert r.status_code == 200, r.text


def test_带显示条件的必填题_发到填表页由页面实时决定露出(client, admin, db, take_student):
    """第二步发题只按岗位筛，带显示条件的题照样发下去。

    发题这一刻还没有答案，照着显示条件筛就会把带条件的题连同挂在它下面的题
    一并筛掉：选了触发选项也出不来，题上写了必填就卡死在「为必填」上，志愿者
    连题都没见过。露出与否交给页面按作答实时判（见 QuestionFields.vue）。
    """
    act_id, _ids, _q = _create_screened(client, admin, db, status="draft", shifts=0)
    _gid, pids = _pick_shift(client, admin, act_id)
    base = client.get(f"/api/staff/activities/{act_id}/form", headers=admin).json()
    yes_no = base["questions"][0]        # 单选：参加过吗
    r = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                   json={"questions": [
                       {"id": yes_no["id"], "kind": "single", "title": "参加过吗",
                        "options": ["是", "否"]},
                       {"kind": "text", "title": "上次做了什么", "required": True,
                        "show_if": {"question_id": yes_no["id"], "values": ["是"]}},
                       {"kind": "text", "title": "只问第二个岗位", "required": True,
                        "applies_to_positions": [pids[1]]},
                   ]})
    assert r.status_code == 200, r.text
    follow = next(x["id"] for x in r.json()["questions"]
                  if x["title"] == "上次做了什么")
    db.get(Activity, act_id).status = "published"
    db.commit()

    t = login(client, take_student())
    d = client.get(f"/api/activities/{act_id}/form?positions={pids[0]}",
                   headers=t).json()
    titles = [x["title"] for x in d["questions"]]
    # 带条件的题也在，岗位范围之外的题照旧不在
    assert "上次做了什么" in titles and "只问第二个岗位" not in titles

    # 选了触发选项，这道题就是必填；不填交不了，填了才过
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": [pids[0]], "answers": {yes_no["id"]: "是"}})
    assert r.status_code == 400 and "上次做了什么" in r.json()["detail"]
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": [pids[0]],
                          "answers": {yes_no["id"]: "是", follow: "带过路"}})
    assert r.status_code == 200, r.text


def test_题目按适用岗位出现(client, admin, db, take_student):
    """挂了适用岗位的题，只问选了那个岗位的人：没选就不出现、不校验、不存。"""
    act_id, _ids, q = _create_screened(client, admin, db, status="draft", shifts=0)
    gid, pids = _pick_shift(client, admin, act_id)
    only_for_first = {"kind": "text", "title": "有无扩音器使用经验",
                      "required": True, "options": [],
                      "applies_to_positions": [pids[0]]}
    r = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                   json={"questions": [{"id": x["id"], "kind": x["kind"],
                                        "title": x["title"], "options": x["options"],
                                        "required": x["required"],
                                        "show_if": x["show_if"]} for x in q]
                                      + [only_for_first]})
    assert r.status_code == 200, r.text
    q2 = r.json()["questions"]
    extra = next(x for x in q2 if x["title"] == "有无扩音器使用经验")
    assert extra["applies_to_positions"] == [pids[0]]
    db.get(Activity, act_id).status = "published"
    db.commit()

    # 只勾第二个岗位：第二步的题目里没有它，不填也交得了
    t = login(client, take_student())
    d = client.get(f"/api/activities/{act_id}/form?positions={pids[1]}", headers=t).json()
    assert extra["id"] not in [x["id"] for x in d["questions"]]
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": [pids[1]], "answers": _answers(q)})
    assert r.status_code == 200, r.text

    # 勾了第一个岗位：这道题是必填，不填交不了
    t2 = login(client, take_student())
    d = client.get(f"/api/activities/{act_id}/form?positions={pids[0]}", headers=t2).json()
    assert extra["id"] in [x["id"] for x in d["questions"]]
    r = client.post(f"/api/activities/{act_id}/apply", headers=t2,
                    json={"position_ids": [pids[0]], "answers": _answers(q)})
    assert r.status_code == 400 and "扩音器" in r.json()["detail"]
    r = client.post(f"/api/activities/{act_id}/apply", headers=t2,
                    json={"position_ids": [pids[0]],
                          "answers": {**_answers(q), extra["id"]: "用过"}})
    assert r.status_code == 200, r.text
    app = (db.query(Application)
           .filter(Application.activity_id == act_id).order_by(
               Application.created_at.desc()).first())
    assert app.answers[extra["id"]] == "用过"


# ======================= 适用岗位：占位岗位与被删掉的岗位 =======================

def _form_with(q, extra):
    """整份表：原有题目原样带上，末尾再加一道新题。"""
    return [{"id": x["id"], "kind": x["kind"], "title": x["title"],
             "options": x["options"], "required": x["required"],
             "show_if": x["show_if"]} for x in q] + [extra]


def _save_form(client, admin, act_id, q, extra):
    return client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                      json={"questions": _form_with(q, extra)})


def _my_answers(db, act_id, sid):
    u = db.query(User).filter(User.student_id == sid).one()
    return (db.query(Application)
            .filter(Application.activity_id == act_id,
                    Application.user_id == u.id).one()).answers


def test_出题不能挂待分配占位岗位(client, admin, db):
    """占位岗位迟早会被删（开放选岗、切回「报名即选岗位」都删它），
    题目挂上去就是给以后埋一个悬空的 id：出题下拉不给它，保存时也拦下来。"""
    act_id, ids, q = _create_screened(client, admin, db, status="draft", shifts=1)
    gid, pids = _pick_shift(client, admin, act_id)
    holder = _holders(db, ids)[0]

    base = client.get(f"/api/staff/activities/{act_id}/form", headers=admin).json()
    by_shift = {g["id"]: [p["id"] for p in g["positions"]] for g in base["shifts"]}
    assert by_shift[ids[0]] == [], "统一分配的场次只列真实岗位，占位岗位不出现"
    assert by_shift[gid] == pids

    r = _save_form(client, admin, act_id, q,
                   {"kind": "text", "title": "只在待分配的人答", "required": False,
                    "applies_to_positions": [holder]})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "待分配" in detail and "不是真岗位" in detail and "请选具体岗位" in detail
    # 整份表都没落库：还是原来那几道题
    assert [x["title"] for x in client.get(
        f"/api/staff/activities/{act_id}/form", headers=admin).json()["questions"]] \
        == [x["title"] for x in q]


def test_挂真实岗位的题目只问那个岗位的人(client, admin, db, take_student):
    act_id, _ids, q = _create_screened(client, admin, db, status="draft", shifts=0)
    _gid, pids = _pick_shift(client, admin, act_id)
    extra = {"kind": "text", "title": "有无扩音器使用经验",
             "applies_to_positions": [pids[0]]}
    r = _save_form(client, admin, act_id, q, extra)
    assert r.status_code == 200, r.text
    saved = next(x for x in r.json()["questions"] if x["title"] == extra["title"])
    assert saved["applies_to_positions"] == [pids[0]]

    db.get(Activity, act_id).status = "published"
    db.commit()
    # 只勾另一个岗位的人：这道题不出现，不填也交得了
    sid = take_student()
    d = client.get(f"/api/activities/{act_id}/form?positions={pids[1]}",
                   headers=login(client, sid)).json()
    assert saved["id"] not in [x["id"] for x in d["questions"]]
    r = client.post(f"/api/activities/{act_id}/apply", headers=login(client, sid),
                    json={"position_ids": [pids[1]], "answers": _answers(q)})
    assert r.status_code == 200, r.text
    assert saved["id"] not in _my_answers(db, act_id, sid)
    # 勾了这个岗位的人：这道题是必填，答了才存
    sid2 = take_student()
    t2 = login(client, sid2)
    d = client.get(f"/api/activities/{act_id}/form?positions={pids[0]}",
                   headers=t2).json()
    assert saved["id"] in [x["id"] for x in d["questions"]]
    r = client.post(f"/api/activities/{act_id}/apply", headers=t2,
                    json={"position_ids": [pids[0]], "answers": _answers(q)})
    assert r.status_code == 400 and extra["title"] in r.json()["detail"]
    r = client.post(f"/api/activities/{act_id}/apply", headers=t2,
                    json={"position_ids": [pids[0]],
                          "answers": {**_answers(q), saved["id"]: "用过"}})
    assert r.status_code == 200, r.text
    assert _my_answers(db, act_id, sid2)[saved["id"]] == "用过"


def test_发布后删岗位题目的适用岗位跟着修剪(client, admin, db, take_student):
    """发布后问卷改不了，删岗位不能要求先改题：直接删，题目的「适用岗位」里拿掉
    这个岗位。它是唯一的一个时，这道题对谁都不显示，不会变成人人都答。"""
    act_id, _ids, q = _create_screened(client, admin, db, status="draft", shifts=0)
    _gid, pids = _pick_shift(client, admin, act_id,
                             positions=("引导", "登记", "医疗点"))
    r = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                   json={"questions": _form_with(q, {
                       "kind": "text", "title": "引导或登记的人答",
                       "required": True,
                       "applies_to_positions": [pids[0], pids[1]]}) + [
                       {"kind": "text", "title": "只问引导", "required": True,
                        "applies_to_positions": [pids[0]]}]})
    assert r.status_code == 200, r.text
    saved = {x["title"]: x for x in r.json()["questions"]}
    both, only = saved["引导或登记的人答"], saved["只问引导"]

    act = db.get(Activity, act_id)
    act.status, act.editing_since = "published", now()
    db.commit()
    assert _save_form(client, admin, act_id, q,
                      {"kind": "text", "title": "x"}).status_code == 409

    r = client.delete(f"/api/staff/positions/{pids[0]}", headers=admin)
    assert r.status_code == 200, r.text
    form = client.get(f"/api/staff/activities/{act_id}/form", headers=admin).json()
    by_id = {x["id"]: x for x in form["questions"]}
    assert by_id[both["id"]]["applies_to_positions"] == [pids[1]]

    db.get(Activity, act_id).editing_since = None
    db.commit()
    t = login(client, take_student())
    shown = {pid: [x["id"] for x in client.get(
        f"/api/activities/{act_id}/form?positions={pid}", headers=t).json()["questions"]]
        for pid in pids[1:]}
    assert both["id"] in shown[pids[1]] and both["id"] not in shown[pids[2]]
    assert only["id"] not in shown[pids[1]] and only["id"] not in shown[pids[2]]


def test_适用岗位被删的题目拦下重选_对谁都不展示(client, admin, db, take_student):
    """岗位被删掉后，挂着它的题目成了一个悬空的引用：保存整份表时拦下来让管理
    者重选，在这之前它对谁也不显示，更不会退化成人人都答。"""
    from backend.models import Position

    act_id, _ids, q = _create_screened(client, admin, db, status="draft", shifts=0)
    _gid, pids = _pick_shift(client, admin, act_id,
                             positions=("引导", "登记", "医疗点"))
    extra = {"kind": "text", "title": "只问医疗点", "required": True,
             "applies_to_positions": [pids[2]]}
    r = _save_form(client, admin, act_id, q, extra)
    assert r.status_code == 200, r.text
    saved = next(x for x in r.json()["questions"] if x["title"] == extra["title"])

    # 直接改库造出「岗位已经不在」的库状态
    db.delete(db.get(Position, pids[2]))
    db.commit()

    r = _save_form(client, admin, act_id, q, extra)
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert extra["title"] in detail and "已被删除" in detail and "重新选" in detail

    db.get(Activity, act_id).status = "published"
    db.commit()
    # 这题对谁也不显示：勾哪个真实岗位都看不到它，也不拦着交表
    sid = take_student()
    t = login(client, sid)
    for pid in pids[:2]:
        d = client.get(f"/api/activities/{act_id}/form?positions={pid}",
                       headers=t).json()
        assert saved["id"] not in [x["id"] for x in d["questions"]]
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": [pids[0]], "answers": _answers(q)})
    assert r.status_code == 200, r.text
    assert saved["id"] not in _my_answers(db, act_id, sid)


def test_填表申请_必填校验_只能填一次(client, admin, db, take_student):
    act_id, ids, q = _create_screened(client, admin, db)
    t = login(client, take_student())
    form = client.get(f"/api/activities/{act_id}/form", headers=t).json()
    assert [x["title"] for x in form["questions"]] == ["参加过吗", "能做什么", "介绍自己", "证明材料"]
    assert form["mine"] is None

    # 直接报名的口子关着
    r = client.post(f"/api/activities/{act_id}/shifts/{ids[0]}/enroll", headers=t,
                    json={"position_id": None})
    assert r.status_code == 409 and "报名问卷" in r.json()["detail"]

    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": _holders(db, ids), "answers": _answers(q, 参加过吗=None)})
    assert r.status_code == 400 and "参加过吗" in r.json()["detail"]
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": [], "answers": _answers(q)})
    assert r.status_code == 400
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": _holders(db, ids), "answers": _answers(q)})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": _holders(db, ids), "answers": _answers(q)})
    assert r.status_code == 409

    d = client.get(f"/api/activities/{act_id}", headers=t).json()
    mine = d["application"]["mine"]
    assert mine["status"] == "applied"
    assert {w["shift_id"] for w in mine["wanted"]} == set(ids)
    # 我的报名里每个意向场次一条「待筛选」
    rows = client.get("/api/me/enrollments", headers=t).json()
    assert sorted(r["status"] for r in rows if r["activity"]["id"] == act_id) == ["applied", "applied"]
    assert all(not r["contact_group_qr"]
               for r in rows if r["activity"]["id"] == act_id)


def test_申请也带调剂意愿_默认接受(client, admin, db, take_student):
    """申请表单新增的勾选（默认勾上）：传 false 就存 false，不传按接受——
    和即选即得报名同一个字段，排班时用来提示与留痕。"""
    act_id, _ids, q = _create_screened(client, admin, db, status="draft", shifts=0)
    _gid, pids = _pick_shift(client, admin, act_id)
    db.get(Activity, act_id).status = "published"
    db.commit()

    sid = take_student()
    r = client.post(f"/api/activities/{act_id}/apply", headers=login(client, sid),
                    json={"position_ids": [pids[0]], "answers": _answers(q),
                          "allow_reassign": False})
    assert r.status_code == 200, r.text
    db.expire_all()
    mine = (db.query(Enrollment)
            .filter(Enrollment.activity_id == act_id,
                    Enrollment.user_id == student_row(db, sid).id).all())
    assert [e.allow_reassign for e in mine] == [False]

    sid2 = take_student()
    r = client.post(f"/api/activities/{act_id}/apply", headers=login(client, sid2),
                    json={"position_ids": [pids[1]], "answers": _answers(q)})
    assert r.status_code == 200, r.text
    db.expire_all()
    other = (db.query(Enrollment)
             .filter(Enrollment.activity_id == act_id,
                     Enrollment.user_id == student_row(db, sid2).id).all())
    assert [e.allow_reassign for e in other] == [True]


def test_申请时就拦时段冲突(client, admin, db, enrolled_activity, take_student):
    act, shift, u = enrolled_activity
    mine = login(client, u.student_id)
    act_id, ids, q = _create_screened(client, admin, db, shifts=1)
    # 把筛选活动的场次挪到跟已报名的场次同一时段
    from backend.models import Shift
    g = db.get(Shift, ids[0])
    g.start_time, g.end_time = shift.start_time, shift.end_time
    db.commit()
    r = client.post(f"/api/activities/{act_id}/apply", headers=mine,
                    json={"position_ids": _holders(db, ids), "answers": _answers(q)})
    assert r.status_code == 409 and r.json()["detail"]["conflicts"]


def test_撤回申请后可以重新填(client, admin, db, take_student):
    """撤回等于没申请过：改答案、改意向场次都是先撤回再重填。"""
    act_id, ids, q = _create_screened(client, admin, db)
    t = login(client, take_student())
    client.post(f"/api/activities/{act_id}/apply", headers=t,
                json={"position_ids": _holders(db, ids), "answers": _answers(q)})
    rows = client.get("/api/me/enrollments", headers=t).json()
    enr_id = next(r["id"] for r in rows if r["activity"]["id"] == act_id)
    # 意向记录不能走退选，只能撤回申请
    assert client.delete(f"/api/activities/enrollments/{enr_id}",
                         headers=t).status_code == 409
    assert client.delete(f"/api/activities/{act_id}/apply", headers=t).status_code == 200
    assert client.delete(f"/api/activities/{act_id}/apply", headers=t).status_code == 409

    d = client.get(f"/api/activities/{act_id}", headers=t).json()
    assert d["application"]["mine"] is None
    assert not [r for r in client.get("/api/me/enrollments", headers=t).json()
                if r["activity"]["id"] == act_id]
    # 重新填一份，只报一个场次
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": _holders(db, ids[:1]), "answers": _answers(q, 参加过吗="否")})
    assert r.status_code == 200, r.text
    mine = client.get(f"/api/activities/{act_id}", headers=t).json()["application"]["mine"]
    assert len(mine["wanted"]) == 1
    # 管理者的申请名单里只有重填的这一份
    lst = client.get(f"/api/staff/activities/{act_id}/applications", headers=admin).json()
    assert len(lst["applications"]) == 1


def test_录取与未录取(client, admin, db, take_student):
    act_id, ids, q = _create_screened(client, admin, db)
    sids = [take_student() for _ in range(3)]
    for sid in sids:
        r = client.post(f"/api/activities/{act_id}/apply", headers=login(client, sid),
                        json={"position_ids": _holders(db, ids), "answers": _answers(q)})
        assert r.status_code == 200, r.text

    lst = client.get(f"/api/staff/activities/{act_id}/applications", headers=admin).json()
    assert len(lst["applications"]) == 3
    first = lst["applications"][0]
    assert first["status"] == "applied" and len(first["wanted"]) == 2
    assert [a["value"] for a in first["answers"]][:2] == ["是", ["引导", "登记"]]
    app_ids = [a["id"] for a in lst["applications"]]

    # 录取前两人到第二场；第三人不录取
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": app_ids[:2], "accept": True,
                          "targets": [{"shift_id": ids[1]}]})
    assert r.status_code == 200 and r.json()["count"] == 2
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": app_ids[2:], "accept": False})
    assert r.status_code == 200
    # 处理过的不能再处理
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": app_ids[:1], "accept": False})
    assert r.status_code == 409

    u0 = db.query(User).filter(User.student_id == sids[0]).one()
    rows = db.query(Enrollment).filter(Enrollment.activity_id == act_id,
                                       Enrollment.user_id == u0.id).all()
    by_shift = {r.shift_id: r for r in rows}
    assert by_shift[ids[1]].status == "enrolled"
    assert by_shift[ids[1]].position_id is not None      # 落在占位岗位上
    assert by_shift[ids[0]].status == "cancelled"
    assert by_shift[ids[0]].cancel_type == "not_chosen"
    n = (db.query(Notification)
         .filter(Notification.user_id == u0.id,
                 Notification.title.like("志愿活动录取通知｜%")).one())
    assert "第2场（" in n.content and "具体岗位将于稍后公布" in n.content

    # 录取的人：我的报名里只剩录到的那一场，联络群二维码随时点得开
    mine = client.get("/api/me/enrollments", headers=login(client, sids[0])).json()
    mine = [r for r in mine if r["activity"]["id"] == act_id]
    assert len(mine) == 1 and mine[0]["status"] == "enrolled"
    assert mine[0]["contact_group_qr"] == "data:qr"

    # 未录取：通知留余地，档案里不留痕
    u2 = db.query(User).filter(User.student_id == sids[2]).one()
    n = (db.query(Notification)
         .filter(Notification.user_id == u2.id,
                 Notification.title.like("志愿活动报名结果｜%")).one())
    assert "暂未能" in n.content
    rows2 = client.get("/api/me/enrollments", headers=login(client, sids[2])).json()
    assert {r["cancel_type"] for r in rows2 if r["activity"]["id"] == act_id} == {"rejected"}
    archive = client.get(f"/api/staff/users/{u2.id}/archive", headers=admin).json()
    assert archive["violations"] == []
    assert act_id not in [h["activity_id"] for h in archive["history"]]


def test_录取名额不够先拦下(client, admin, db, take_student):
    act_id, ids, q = _create_screened(client, admin, db, shifts=1)
    from backend.models import Shift
    holder = db.get(Shift, ids[0]).placeholder()
    holder.quota = 1
    db.commit()
    for _ in range(2):
        t = login(client, take_student())
        r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                        json={"position_ids": _holders(db, ids), "answers": _answers(q)})
        assert r.status_code == 200, r.text
    lst = client.get(f"/api/staff/activities/{act_id}/applications", headers=admin).json()
    app_ids = [a["id"] for a in lst["applications"]]
    assert len(app_ids) == 2
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": app_ids, "accept": True})
    assert r.status_code == 409 and "名额" in r.json()["detail"]
    # 整批拦下，不是录一个算一个：两份申请都还在待筛选，谁都没被录上
    lst = client.get(f"/api/staff/activities/{act_id}/applications", headers=admin).json()
    assert [a["status"] for a in lst["applications"]] == ["applied", "applied"]
    assert _placed(client, admin, act_id) == {a: [] for a in app_ids}


def _decide(client, admin, act_id, app_id, picks):
    return client.post(f"/api/staff/activities/{act_id}/applications/decide",
                       headers=admin,
                       json={"application_ids": [app_id], "accept": True,
                             "picks": {app_id: picks}})


def _accept_note(db, user_id):
    return (db.query(Notification)
            .filter(Notification.user_id == user_id,
                    Notification.title.like("志愿活动录取通知｜%")).one())


def test_一份申请录到多个场次_没选中的意向作废(client, admin, db, take_student):
    from backend.models import Shift
    act_id, ids, q = _create_screened(client, admin, db, status="draft", shifts=3)
    gid, pids = _pick_shift(client, admin, act_id)
    db.get(Activity, act_id).status = "published"
    db.commit()
    sid = take_student()
    r = client.post(f"/api/activities/{act_id}/apply", headers=login(client, sid),
                    json={"position_ids": _holders(db, ids) + [pids[0]],
                          "answers": _answers(q)})
    assert r.status_code == 200, r.text
    app_id = client.get(f"/api/staff/activities/{act_id}/applications",
                        headers=admin).json()["applications"][0]["id"]

    # 统一分配的第 1 场 + 选岗场（改到「登记」），其余两场意向作废
    r = _decide(client, admin, act_id, app_id,
                [{"shift_id": ids[0]}, {"shift_id": gid, "position_id": pids[1]}])
    assert r.status_code == 200, r.text
    assert sorted(_placed(client, admin, act_id)[app_id]) == ["待分配", "登记"]
    u = db.query(User).filter(User.student_id == sid).one()
    by_shift = {e.shift_id: e for e in db.query(Enrollment)
                .filter(Enrollment.activity_id == act_id, Enrollment.user_id == u.id)}
    assert by_shift[ids[0]].status == by_shift[gid].status == "enrolled"
    for other in ids[1:]:
        assert (by_shift[other].status, by_shift[other].cancel_type) \
            == ("cancelled", "not_chosen")
    assert db.get(Application, app_id).status == "accepted"
    # 我的报名里只列录到的两场
    mine = [r for r in client.get("/api/me/enrollments", headers=login(client, sid)).json()
            if r["activity"]["id"] == act_id]
    assert sorted(r["status"] for r in mine) == ["enrolled", "enrolled"]

    # 通知只有一条，逐场写实际岗位
    g0, gp = db.get(Shift, ids[0]), db.get(Shift, gid)
    n = _accept_note(db, u.id)
    assert f"{g0.name}（{g0.span_text()}）· 具体岗位将于稍后公布" in n.content
    assert f"{gp.name}（{gp.span_text()}）· 岗位：登记" in n.content
    assert "以负责人后续分配为准" not in n.content
    assert db.get(Shift, ids[1]).name not in n.content
    assert "联络群二维码" in n.content


def test_录取时选中的场次时间重叠整份拦下(client, admin, db, take_student):
    """申请时意向可以重叠（他只是说这几场都能来）；录取时一个人不能同时在两处。"""
    from backend.models import Shift
    act_id, ids, q = _create_screened(client, admin, db, shifts=2)
    a, b = db.get(Shift, ids[0]), db.get(Shift, ids[1])
    b.start_time = a.start_time + timedelta(hours=1)
    b.end_time = a.end_time + timedelta(hours=1)
    db.commit()
    sid = take_student()
    u, app = _apply(client, db, act_id, q, sid, ids)

    r = _decide(client, admin, act_id, app.id,
                [{"shift_id": ids[0]}, {"shift_id": ids[1]}])
    assert r.status_code == 409
    assert "「第1场」" in r.json()["detail"] and "「第2场」" in r.json()["detail"]
    db.refresh(app)
    assert app.status == "applied"
    assert {e.status for e in db.query(Enrollment)
            .filter(Enrollment.activity_id == act_id)} == {"applied"}

    # 只录一场就行：他自己另一场的意向记录不算冲突
    r = _decide(client, admin, act_id, app.id, [{"shift_id": ids[1]}])
    assert r.status_code == 200, r.text
    n = _accept_note(db, u.id)
    assert "第2场" in n.content and "第1场" not in n.content


def test_录取时和已报名的其他活动冲突也拦(client, admin, db, enrolled_activity):
    from backend.models import Shift
    _act, shift, u = enrolled_activity
    act_id, ids, q = _create_screened(client, admin, db, shifts=2)
    _u, app = _apply(client, db, act_id, q, u.student_id, ids)
    # 申请之后场次改了时间，撞上他已报名的那一场
    g = db.get(Shift, ids[0])
    g.start_time, g.end_time = shift.start_time, shift.end_time
    db.commit()

    r = _decide(client, admin, act_id, app.id, [{"shift_id": ids[0]}])
    assert r.status_code == 409 and "时间冲突" in r.json()["detail"]
    r = _decide(client, admin, act_id, app.id,
                [{"shift_id": ids[0]}, {"shift_id": ids[1]}])
    assert r.status_code == 409
    r = _decide(client, admin, act_id, app.id, [{"shift_id": ids[1]}])
    assert r.status_code == 200, r.text


def test_多场次录取其中一场名额不足整份拦下(client, admin, db, take_student):
    from backend.models import Shift
    act_id, ids, q = _create_screened(client, admin, db, shifts=2)
    db.get(Shift, ids[1]).placeholder().quota = 1
    db.commit()
    _ua, first = _apply(client, db, act_id, q, take_student(), ids[1:])
    ub, second = _apply(client, db, act_id, q, take_student(), ids)
    assert _decide(client, admin, act_id, first.id,
                   [{"shift_id": ids[1]}]).status_code == 200

    r = _decide(client, admin, act_id, second.id,
                [{"shift_id": ids[0]}, {"shift_id": ids[1]}])
    assert r.status_code == 409 and "第2场" in r.json()["detail"]
    # 不是录一场算一场：第 1 场也没落库，申请还在待筛选，没有通知
    db.refresh(second)
    assert second.status == "applied"
    assert {e.status for e in db.query(Enrollment)
            .filter(Enrollment.user_id == ub.id)} == {"applied"}
    assert (db.query(Notification).filter(Notification.user_id == ub.id).count()) == 0

    # 同一场次给两次、没选场次，都说清楚
    r = _decide(client, admin, act_id, second.id,
                [{"shift_id": ids[0]}, {"shift_id": ids[0]}])
    assert r.status_code == 400 and "同一场次" in r.json()["detail"]
    r = _decide(client, admin, act_id, second.id, [])
    assert r.status_code == 400


def test_公开后报名问卷锁定(client, admin, db):
    act_id, _ids, q = _create_screened(client, admin, db)
    r = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                   json={"questions": [{"kind": "text", "title": "新题"}]})
    assert r.status_code == 409
    assert client.get(f"/api/staff/activities/{act_id}/form",
                      headers=admin).json()["editable"] is False


def test_报名问卷文件只有本人和管理者能下载(client, admin, db, take_student):
    act_id, ids, q = _create_screened(client, admin, db)
    t = login(client, take_student())
    r = client.post(f"/api/activities/{act_id}/apply/upload", headers=t,
                    files={"file": ("证明.pdf", b"%PDF-1.4 demo", "application/pdf")})
    assert r.status_code == 200, r.text
    att_id = r.json()["id"]
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"position_ids": _holders(db, ids), "answers": _answers(q, 证明材料=att_id)})
    assert r.status_code == 200, r.text
    lst = client.get(f"/api/staff/activities/{act_id}/applications", headers=admin).json()
    file_answer = lst["applications"][0]["answers"][3]["value"]
    assert file_answer["attachment_id"] == att_id
    assert client.get(f"/api/materials/{att_id}", headers=admin).status_code == 200
    assert client.get(f"/api/materials/{att_id}", headers=t).status_code == 200
    other = login(client, take_student())
    assert client.get(f"/api/materials/{att_id}", headers=other).status_code == 404


def test_复制活动带报名问卷不带联络群(client, admin, db):
    """题目里指着岗位和前一道题的地方，复制后要指向新活动自己的那份。"""
    act_id, _ids, q = _create_screened(client, admin, db, status="draft", shifts=0)
    gid, pids = _pick_shift(client, admin, act_id)
    r = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                   json={"questions": [
                       {"kind": "single", "title": "参加过吗", "options": ["是", "否"]},
                   ]})
    assert r.status_code == 200, r.text
    first = r.json()["questions"][0]["id"]
    r = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                   json={"questions": [
                       {"id": first, "kind": "single", "title": "参加过吗",
                        "options": ["是", "否"]},
                       {"kind": "text", "title": "只问引导岗",
                        "applies_to_positions": [pids[0]]},
                       {"kind": "text", "title": "上次做了什么",
                        "show_if": {"question_id": first, "values": ["是"]}},
                   ]})
    assert r.status_code == 200, r.text

    r = client.post(f"/api/staff/activities/{act_id}/copy", headers=admin,
                    json={"shift_days": 3, "title": "副本", "contact_group_qr": "data:new"})
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    d = client.get(f"/api/activities/{new_id}", headers=admin).json()
    assert d["recruit_mode"] == "screened" and d["contact_group_qr"] == "data:new"
    qs = d["application"]["questions"]
    assert [x["title"] for x in qs] == ["参加过吗", "只问引导岗", "上次做了什么"]
    new_positions = {p["id"] for g in d["shifts"] for p in g["positions"]}
    # 适用岗位换成了副本自己的岗位
    assert qs[1]["applies_to_positions"] and pids[0] not in qs[1]["applies_to_positions"]
    assert all(p in new_positions for p in qs[1]["applies_to_positions"])
    # 显示条件指向副本自己的那道题，不是原活动的
    assert qs[2]["show_if"]["question_id"] == qs[0]["id"] != first


def test_报名成功即弹联络群(client, admin, db, enrolled_activity, take_student):
    act, shift, _u = enrolled_activity
    t = login(client, take_student())
    r = client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll", headers=t,
                    json={"position_id": shift.positions[0].id})
    assert r.status_code == 200, r.text
    assert r.json()["contact_group_qr"] == "x"
    rows = client.get("/api/me/enrollments", headers=t).json()
    row = next(x for x in rows if x["activity"]["id"] == act.id)
    assert row["contact_group_qr"] == "x"
    # 志愿者看的活动详情里没有二维码
    assert "contact_group_qr" not in client.get(f"/api/activities/{act.id}", headers=t).json()


def test_报名问卷结果导出excel(client, admin, db, take_student):
    """一人一行、一题一列；被条件挡掉的题留空。"""
    from io import BytesIO
    from openpyxl import load_workbook

    act_id, ids, q = _create_screened(client, admin, db)
    sids = [take_student() for _ in range(2)]
    for i, sid in enumerate(sids):
        r = client.post(f"/api/activities/{act_id}/apply", headers=login(client, sid),
                        json={"position_ids": _holders(db, ids[:1] if i else ids),
                              "answers": _answers(q), "leader_willing": bool(i)})
        assert r.status_code == 200, r.text
    lst = client.get(f"/api/staff/activities/{act_id}/applications", headers=admin).json()
    # 序号是交申请的先后：提交时间只到分钟，同一分钟交的靠它分
    assert [a["seq"] for a in lst["applications"]] == [1, 2]
    client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                json={"application_ids": [lst["applications"][0]["id"]],
                      "accept": True, "targets": [{"shift_id": ids[0]}]})

    r = client.get(f"/api/staff/activities/{act_id}/applications.xlsx", headers=admin)
    assert r.status_code == 200
    ws = load_workbook(BytesIO(r.content)).active
    rows = list(ws.values)
    header = list(rows[0])
    assert header[:3] == ["序号", "姓名", "学号"]
    assert [row[0] for row in rows[1:]] == [1, 2]
    assert header[-4:] == ["参加过吗", "能做什么", "介绍自己", "证明材料"]
    assert "内部提示分" in header and "组长意愿" in header
    assert len(rows) == 3
    first = dict(zip(header, rows[1]))
    assert first["参加过吗"] == "是" and first["能做什么"] == "引导、登记"
    assert first["录取结果"] == "已录取" and "（录取）" in first["意向场次"]
    assert dict(zip(header, rows[2]))["组长意愿"] == "愿意"

    # 即选即得的活动没有报名问卷
    other = client.post("/api/staff/activities", headers=admin,
                        json={"title": f"即选即得 {now():%H%M%S%f}", "organizer": "校青协",
                              "intro": "x", "location": "东门", "contact_group_qr": "q",
                              "contacts": [{"name": "王", "phone": "13900000000"}],
                              "reg_start": now().isoformat(),
                              "reg_end": (now() + timedelta(days=1)).isoformat()}).json()["id"]
    assert client.get(f"/api/staff/activities/{other}/applications.xlsx",
                      headers=admin).status_code == 409


# ======================= 即选即得也能设报名问卷 =======================

def test_即选即得的报名问卷在报名时答(client, admin, db, open_group, take_student):
    """题目挂在活动上，报名弹窗里答；答案记在报名记录上，名单和导出都看得到。"""
    from backend.models import Activity as Act

    act, g = open_group(3)
    db.get(Act, act.id).status = "draft"
    db.commit()
    r = client.put(f"/api/staff/activities/{act.id}/form", headers=admin,
                   json={"questions": [
                       {"kind": "single", "title": "会开车吗", "options": ["会", "不会"]},
                       {"kind": "text", "title": "紧急联系人", "required": True},
                   ]})
    assert r.status_code == 200, r.text
    q_drive, q_contact = (q["id"] for q in r.json()["questions"])
    db.get(Act, act.id).status = "published"
    db.commit()

    h = login(client, take_student())
    d = client.get(f"/api/activities/{act.id}", headers=h).json()
    assert [q["title"] for q in d["questions"]] == ["会开车吗", "紧急联系人"]
    url = f"/api/activities/{act.id}/shifts/{g.id}/enroll"
    r = client.post(url, headers=h, json={"position_id": g.test_position_id,
                                          "answers": {q_drive: "会"}})
    assert r.status_code == 400 and "紧急联系人" in r.json()["detail"]
    r = client.post(url, headers=h, json={"position_id": g.test_position_id,
                                          "answers": {q_drive: "会",
                                                      q_contact: "张三 13800000000"}})
    assert r.status_code == 200, r.text

    roster = client.get(f"/api/staff/shifts/{g.id}/roster", headers=admin).json()
    assert [q["title"] for q in roster["questions"]] == ["会开车吗", "紧急联系人"]
    row = roster["enrolled"][0]
    assert [a["value"] for a in row["answers"]] == ["会", "张三 13800000000"]


def _apply_with_file(client, db, act_id, ids, q, sid, data=b"%PDF-1.4 fake"):
    h = login(client, sid)
    r = client.post(f"/api/activities/{act_id}/apply/upload", headers=h,
                    files={"file": ("证明.pdf", data, "application/pdf")})
    assert r.status_code == 200, r.text
    att_id = r.json()["id"]
    r = client.post(f"/api/activities/{act_id}/apply", headers=h,
                    json={"position_ids": _holders(db, ids[:1]),
                          "answers": _answers(q, 证明材料=att_id)})
    assert r.status_code == 200, r.text
    return att_id


def test_报名问卷导出只给excel附件另外打包(client, admin, db, take_student):
    """导出只有 .xlsx，附件列写附件包里的文件名（不加超链接）；
    附件数走 JSON 接口，附件包里按「姓名_学号_题目」命名，与表里一字不差。"""
    import zipfile
    from io import BytesIO
    from openpyxl import load_workbook

    act_id, ids, q = _create_screened(client, admin, db)
    sid, sid2 = take_student(), take_student()
    _apply_with_file(client, db, act_id, ids, q, sid, b"%PDF-1.4 one")
    _apply_with_file(client, db, act_id, ids, q, sid2, b"%PDF-1.4 two")
    base = f"/api/staff/activities/{act_id}"

    r = client.get(dl_url(client, admin, f"{base}/applications.xlsx"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert ".xlsx" in r.headers["content-disposition"]
    ws = load_workbook(BytesIO(r.content)).active
    header = list(ws.values)[0]
    col, sid_col = header.index("证明材料") + 1, header.index("学号") + 1
    cells = {ws.cell(row=i, column=sid_col).value: ws.cell(row=i, column=col)
             for i in (2, 3)}
    u, u2 = student_row(db, sid), student_row(db, sid2)
    name = f"{u.name}_{u.student_id}_证明材料.pdf"
    name2 = f"{u2.name}_{u2.student_id}_证明材料.pdf"
    assert cells[u.student_id].value == name and cells[u.student_id].hyperlink is None
    assert cells[u2.student_id].value == name2

    r = client.get(f"{base}/applications/attachments", headers=admin)
    assert r.status_code == 200 and r.json() == {"count": 2}

    r = client.get(dl_url(client, admin, f"{base}/applications/attachments.zip"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "_附件_" in unquote(r.headers["content-disposition"])
    z = zipfile.ZipFile(BytesIO(r.content))
    assert sorted(z.namelist()) == sorted([name, name2])
    assert z.read(name) == b"%PDF-1.4 one" and z.read(name2) == b"%PDF-1.4 two"


def test_报名问卷没有附件时附件包404(client, admin, db, take_student):
    """没有文件答案、或文件在磁盘上已经不在了：数目为 0，附件包 404，表照常导出。"""
    from backend.exporter import MATERIAL_DIR
    from backend.models import Attachment

    act_id, ids, q = _create_screened(client, admin, db)
    base = f"/api/staff/activities/{act_id}"
    h = login(client, take_student())
    r = client.post(f"/api/activities/{act_id}/apply", headers=h,
                    json={"position_ids": _holders(db, ids[:1]), "answers": _answers(q)})
    assert r.status_code == 200, r.text
    assert client.get(f"{base}/applications/attachments",
                      headers=admin).json() == {"count": 0}
    r = client.get(dl_url(client, admin, f"{base}/applications/attachments.zip"))
    assert r.status_code == 404

    # 文件被删了：跳过，不报错
    att_id = _apply_with_file(client, db, act_id, ids, q, take_student())
    (MATERIAL_DIR / db.get(Attachment, att_id).stored_name).unlink()
    assert client.get(f"{base}/applications/attachments",
                      headers=admin).json() == {"count": 0}
    assert client.get(dl_url(client, admin, f"{base}/applications/attachments.zip")
                      ).status_code == 404
    assert client.get(dl_url(client, admin, f"{base}/applications.xlsx")
                      ).status_code == 200

    # 即选即得的活动没有报名问卷
    other = client.post("/api/staff/activities", headers=admin,
                        json={"title": f"即选即得 {now():%H%M%S%f}", "organizer": "校青协",
                              "intro": "x", "location": "东门", "contact_group_qr": "q",
                              "contacts": [{"name": "王", "phone": "13900000000"}],
                              "reg_start": now().isoformat(),
                              "reg_end": (now() + timedelta(days=1)).isoformat()}).json()["id"]
    assert client.get(f"/api/staff/activities/{other}/applications/attachments",
                      headers=admin).status_code == 409


# ======================= 开场仍未处理的申请自动落选 =======================

def _start_now(db, shift_id):
    """让场次一分钟前已经开始（结束时间照旧在后面）。"""
    from backend.models import Shift
    db.get(Shift, shift_id).start_time = now() - timedelta(minutes=1)
    db.commit()


def _rejected_notes(db, user_id):
    return (db.query(Notification)
            .filter(Notification.user_id == user_id,
                    Notification.title.like("志愿活动报名结果｜%")).count())


def _apply(client, db, act_id, q, sid, shift_ids):
    r = client.post(f"/api/activities/{act_id}/apply", headers=login(client, sid),
                    json={"position_ids": _holders(db, shift_ids), "answers": _answers(q)})
    assert r.status_code == 200, r.text
    u = db.query(User).filter(User.student_id == sid).one()
    return u, db.query(Application).filter(Application.activity_id == act_id,
                                           Application.user_id == u.id).one()


def test_单场次开场仍未处理自动落选_只通知一次(client, admin, db, take_student):
    from backend import scheduled
    from backend.models import AuditLog
    act_id, ids, q = _create_screened(client, admin, db, shifts=1)
    sid = take_student()
    u, app = _apply(client, db, act_id, q, sid, ids)

    # 没开场：什么都不动
    assert scheduled.run_once(db)["undecided"] == 0
    db.refresh(app)
    assert app.status == "applied"

    _start_now(db, ids[0])
    assert scheduled.run_once(db)["undecided"] == 1
    db.refresh(app)
    assert app.status == "rejected" and app.decided_by is None
    assert _rejected_notes(db, u.id) == 1
    n = (db.query(Notification).filter(Notification.user_id == u.id,
                                       Notification.title.like("志愿活动报名结果｜%")).one())
    assert "暂未能" in n.content
    e = db.query(Enrollment).filter(Enrollment.activity_id == act_id,
                                    Enrollment.user_id == u.id).one()
    assert (e.status, e.cancel_type) == ("cancelled", "rejected")
    log = (db.query(AuditLog).filter(AuditLog.activity_id == act_id,
                                     AuditLog.summary.like("%自动落选%")).one())
    assert log.actor_id is None and u.name in log.summary

    # 再跑不重复
    assert scheduled.run_once(db)["undecided"] == 0
    assert _rejected_notes(db, u.id) == 1

    # 管理端看到的就是未录取；再点处理会被拦
    lst = client.get(f"/api/staff/activities/{act_id}/applications", headers=admin).json()
    assert lst["applications"][0]["status"] == "rejected"
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": [app.id], "accept": True})
    assert r.status_code == 409

    # 不算本人退出：档案不留经历、退选画像为空，提示分不扣
    archive = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert act_id not in [h["activity_id"] for h in archive["history"]]
    assert archive["cancels"]["items"] == []
    assert archive["score"]["items"] == []


def test_多场次先开场的只作废那一场_晚的还能录取(client, admin, db, take_student):
    from backend import scheduled
    act_id, ids, q = _create_screened(client, admin, db, shifts=2)
    ua, app_a = _apply(client, db, act_id, q, take_student(), ids)
    ub, app_b = _apply(client, db, act_id, q, take_student(), ids)

    _start_now(db, ids[0])
    assert scheduled.run_once(db)["undecided"] == 0
    for app, u in ((app_a, ua), (app_b, ub)):
        db.refresh(app)
        assert app.status == "applied"
        assert _rejected_notes(db, u.id) == 0
        by_shift = {e.shift_id: e for e in db.query(Enrollment)
                    .filter(Enrollment.activity_id == act_id,
                            Enrollment.user_id == u.id)}
        assert (by_shift[ids[0]].status, by_shift[ids[0]].cancel_type) \
            == ("cancelled", "rejected")
        assert by_shift[ids[1]].status == "applied"

    # 已开场那一场录不进去了；晚的那一场照样能录
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": [app_a.id], "accept": True,
                          "targets": [{"shift_id": ids[0]}]})
    assert r.status_code == 400
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": [app_a.id], "accept": True})
    assert r.status_code == 200, r.text
    assert _placed(client, admin, act_id)[app_a.id] != []

    # 第二场也开了：录取的不受影响，没处理的这时才落选
    _start_now(db, ids[1])
    assert scheduled.run_once(db)["undecided"] == 1
    db.refresh(app_a)
    db.refresh(app_b)
    assert app_a.status == "accepted" and app_b.status == "rejected"
    assert _rejected_notes(db, ua.id) == 0 and _rejected_notes(db, ub.id) == 1
    enr = (db.query(Enrollment).filter(Enrollment.shift_id == ids[1],
                                       Enrollment.user_id == ua.id).one())
    assert enr.status == "enrolled"


def test_已处理的申请开场时不受影响(client, admin, db, take_student):
    from backend import scheduled
    act_id, ids, q = _create_screened(client, admin, db, shifts=1)
    ua, app_a = _apply(client, db, act_id, q, take_student(), ids)
    ub, app_b = _apply(client, db, act_id, q, take_student(), ids)
    for app, accept in ((app_a, True), (app_b, False)):
        r = client.post(f"/api/staff/activities/{act_id}/applications/decide",
                        headers=admin, json={"application_ids": [app.id],
                                             "accept": accept})
        assert r.status_code == 200, r.text

    _start_now(db, ids[0])
    assert scheduled.run_once(db)["undecided"] == 0
    db.refresh(app_a)
    db.refresh(app_b)
    assert app_a.status == "accepted" and app_b.status == "rejected"
    # 手动不录取那一条只有当初那一次通知
    assert _rejected_notes(db, ua.id) == 0 and _rejected_notes(db, ub.id) == 1


def test_录取到两场共用同一培训_培训只报一次(client, admin, db, take_student):
    """两场都叫同一场培训：录取两场后培训只该有一条报名，整批不能报错。"""
    act_id, ids, q = _create_screened(client, admin, db, status="draft", shifts=2)
    start = now().replace(microsecond=0) + timedelta(days=4)
    r = client.post(f"/api/staff/activities/{act_id}/shifts", headers=admin,
                    json={"name": "岗前培训", "kind": "training",
                          "applies_to_positions": [], "hours_mode": "none",
                          "location": "排练厅",
                          "start_time": start.isoformat(),
                          "end_time": (start + timedelta(hours=2)).isoformat()})
    assert r.status_code == 200, r.text
    training = r.json()["id"]
    db.get(Activity, act_id).status = "published"
    db.commit()
    sid = take_student()
    r = client.post(f"/api/activities/{act_id}/apply", headers=login(client, sid),
                    json={"position_ids": _holders(db, ids), "answers": _answers(q)})
    assert r.status_code == 200, r.text
    app_id = client.get(f"/api/staff/activities/{act_id}/applications",
                        headers=admin).json()["applications"][0]["id"]

    r = _decide(client, admin, act_id, app_id,
                [{"shift_id": ids[0]}, {"shift_id": ids[1]}])
    assert r.status_code == 200, r.text
    u = db.query(User).filter(User.student_id == sid).one()
    rows = (db.query(Enrollment)
            .filter(Enrollment.shift_id == training, Enrollment.user_id == u.id).all())
    assert [e.status for e in rows] == ["enrolled"]


def test_新题不用先保存就能当显示条件的来源(client, admin, db):
    """编辑页给新题一个临时编号 ref，后面的题拿它挂条件，一次保存就换成正式 id。"""
    act_id, _ids, _q = _create_screened(client, admin, db, status="draft")
    r = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                   json={"questions": [
                       {"ref": "new-1", "kind": "single", "title": "会外语吗",
                        "options": ["会", "不会"]},
                       {"ref": "new-2", "kind": "text", "title": "会哪几种",
                        "show_if": {"question_id": "new-1", "values": ["会"]}},
                   ]})
    assert r.status_code == 200, r.text
    qs = r.json()["questions"]
    assert qs[1]["show_if"] == {"question_id": qs[0]["id"], "values": ["会"]}
    assert qs[0]["id"] != "new-1"

    # 临时编号指向后面的题，照样按「只能挂在前面」拦下
    bad = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                     json={"questions": [
                         {"ref": "new-2", "kind": "text", "title": "会哪几种",
                          "show_if": {"question_id": "new-1", "values": ["会"]}},
                         {"ref": "new-1", "kind": "single", "title": "会外语吗",
                          "options": ["会", "不会"]}]})
    assert bad.status_code == 400 and "前面" in bad.json()["detail"]
