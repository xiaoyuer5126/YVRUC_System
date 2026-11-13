"""活动的联合主办方（co_organizer）。

组织单位（organizer）必填不变；联合主办方选填，填了对外显示成「A × B」
（× 两侧各一个空格），导出里 A、B 各占一列。归口审批、广场筛选、统计
只看 A——多一个合办方不代表这场活动该归到别处。
"""
from __future__ import annotations

from datetime import timedelta

from backend.models import Activity, now, uid
from conftest import login, upload_qr


def _new_activity(client, headers, **over):
    """建一场活动。organizer 必填，co_organizer 选填。"""
    body = {"contact_group_qr": upload_qr(client, headers), "title": f"联合主办{uid('t')}",
            "organizer": "校青年志愿者协会", "intro": "x", "location": "世纪馆",
            "contacts": [{"name": "王", "phone": "13900000000"}],
            "reg_start": (now() - timedelta(days=1)).isoformat(),
            "reg_end": (now() + timedelta(days=5)).isoformat(), **over}
    return client.post("/api/staff/activities", headers=headers, json=body)


def test_联合主办方选填_组织单位仍必填(client, admin):
    """不填 B 照建；不填 A 还是那句 400。"""
    assert _new_activity(client, admin).status_code == 200
    r = _new_activity(client, admin, organizer="   ")
    assert r.status_code == 400 and r.json()["detail"] == "请填写组织单位"


def test_联合主办方进详情与列表_显示成A乘B(client, admin):
    title = f"合办活动{uid('t')}"
    r = _new_activity(client, admin, title=title,
                      organizer="校青年志愿者协会",
                      co_organizer="  文学院青年志愿者协会  ")
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    text = "校青年志愿者协会 × 文学院青年志愿者协会"     # × 两侧各一个空格
    d = client.get(f"/api/activities/{aid}", headers=admin).json()
    assert d["organizer"] == "校青年志愿者协会"
    assert d["co_organizer"] == "文学院青年志愿者协会"     # 首尾空白不留
    assert d["organizer_text"] == text

    items = client.get(f"/api/staff/activities?q={title}",
                       headers=admin).json()["items"]
    assert len(items) == 1
    assert items[0]["organizer_text"] == text
    assert items[0]["co_organizer"] == "文学院青年志愿者协会"

    # 没合办的活动，显示就是 A 自己
    plain = _new_activity(client, admin, title=f"单独主办{uid('t')}",
                          organizer="图书馆").json()["id"]
    d = client.get(f"/api/activities/{plain}", headers=admin).json()
    assert d["co_organizer"] == "" and d["organizer_text"] == "图书馆"


def test_改联合主办方_留痕写中文名也能清空(client, admin):
    aid = _new_activity(client, admin, title=f"改合办方{uid('t')}").json()["id"]
    r = client.patch(f"/api/staff/activities/{aid}", headers=admin,
                     json={"co_organizer": "  校团委  "})
    assert r.status_code == 200, r.text
    change = next(c for c in r.json()["changes"] if c["field"] == "co_organizer")
    assert change["label"] == "联合主办方"
    assert change["old"] == "" and change["new"] == "校团委"
    d = client.get(f"/api/activities/{aid}", headers=admin).json()
    assert d["co_organizer"] == "校团委"
    assert d["organizer_text"] == "校青年志愿者协会 × 校团委"

    # 清空 = 回到单独主办，显示里不留「×」
    r = client.patch(f"/api/staff/activities/{aid}", headers=admin,
                     json={"co_organizer": ""})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/activities/{aid}", headers=admin).json()
    assert d["co_organizer"] == "" and d["organizer_text"] == "校青年志愿者协会"


def test_广场筛选仍按组织单位_带合办方的也在(client, db, open_group, take_student):
    """筛选与候选项：组织单位照旧按 A，合办方只多一份输入建议。"""
    a_tag, b_tag = uid("org"), uid("co")
    act, _g = open_group(2)
    row = db.get(Activity, act.id)
    row.organizer, row.co_organizer, row.title = a_tag, b_tag, f"{a_tag} 合办"
    db.commit()
    h = login(client, take_student())

    d = client.get(f"/api/activities?organizer={a_tag}&status=", headers=h).json()
    assert [i["id"] for i in d["items"]] == [act.id]
    assert d["items"][0]["organizer_text"] == f"{a_tag} × {b_tag}"
    assert a_tag in d["organizers"] and b_tag in d["co_organizers"]
    # 合办方不是筛选依据
    assert client.get(f"/api/activities?organizer={b_tag}&status=",
                      headers=h).json()["total"] == 0


def test_复制活动带上联合主办方(client, admin):
    aid = _new_activity(client, admin, title=f"待复制{uid('t')}",
                        co_organizer="文学院青年志愿者协会").json()["id"]
    r = client.post(f"/api/staff/activities/{aid}/copy", headers=admin,
                    json={"shift_days": 7, "title": f"复制件{uid('t')}",
                          "contact_group_qr": upload_qr(client, admin)})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/activities/{r.json()['id']}", headers=admin).json()
    assert d["co_organizer"] == "文学院青年志愿者协会"
    assert d["organizer_text"] == "校青年志愿者协会 × 文学院青年志愿者协会"


def test_联合主办方最长64字(client, admin):
    """跟组织单位同一个列宽：65 字被模型拦下，64 字收得下。"""
    assert _new_activity(client, admin, co_organizer="文" * 65).status_code == 422
    ok = "文" * 64
    r = _new_activity(client, admin, co_organizer=ok)
    assert r.status_code == 200, r.text
    d = client.get(f"/api/activities/{r.json()['id']}", headers=admin).json()
    assert d["co_organizer"] == ok
