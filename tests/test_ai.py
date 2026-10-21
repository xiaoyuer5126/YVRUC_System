"""智能起草（预览版）：没开不让用、原文长短与限频、模型输出按白名单收拾、缺项由平台自己数。

不连真模型：把 httpx.post 换成假的，验的是平台这一侧怎么问、怎么收。
"""
from __future__ import annotations

import json

import httpx
import pytest

from backend import ai, config, locks
from backend.models import Activity
from conftest import POSITION_TEXT, login, make_draft


class _Resp:
    def __init__(self, status: int, content):
        self.status_code = status
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture
def model(monkeypatch):
    """开着 AI，并记下发给模型的请求；replies 里放模型这次要回的内容。"""
    monkeypatch.setattr(config, "AI_API_KEY", "sk-test")
    calls, replies = [], []

    def fake_post(url, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "body": json})
        status, content = replies.pop(0)
        return _Resp(status, content)

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    # 限频计数在 Redis 里跨用例、跨轮次累积，一小时内多跑几遍就会撞上限：这里不计
    monkeypatch.setattr(locks, "rate_count", lambda key: 0)
    monkeypatch.setattr(locks, "note_rate", lambda key, window: None)
    return calls, replies


TEXT = "校运动会开幕式志愿者招募，10 月 18 日上午在世纪馆，需要检录引导 6 人。"


def test_没配key就不能用_常量里也说没开(client, admin, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "")
    assert client.get("/api/auth/constants", headers=admin).json()["ai_enabled"] is False
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": TEXT})
    assert r.status_code == 409


def test_学生不能用(client, take_student, model):
    r = client.post("/api/staff/ai/activity-draft", headers=login(client, take_student()),
                    data={"text": TEXT})
    assert r.status_code == 403


def test_活动草稿按白名单收拾_空值不回(client, admin, model):
    calls, replies = model
    replies.append((200, json.dumps({
        "title": "校运动会开幕式志愿服务", "organizer": "校团委", "location": "世纪馆",
        "intro": "开幕式现场引导", "volunteer_requirement": "",
        "volunteer_benefit": "",
        "contacts": [{"name": "王老师", "phone": "13900000000"}, {"name": "", "phone": ""}],
        "recruit_mode": "assign",                      # 不认识的类型丢掉
        "recruit_campuses": ["中关村校区", "海淀校区"],   # 不在可选集合里的丢掉
        "recruit_stages": ["本科生"],
        "reg_start": "", "reg_end": "2026-10-15 23:59",
        "notes": ["原文没写报名开始时间"],
        "password": "不该透传的东西",
    }, ensure_ascii=False)))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": TEXT})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["draft"] == {
        "title": "校运动会开幕式志愿服务", "organizer": "校团委", "location": "世纪馆",
        "intro": "开幕式现场引导",
        "contacts": [{"name": "王老师", "phone": "13900000000"}],
        "recruit_campuses": ["中关村校区"], "recruit_stages": ["本科生"],
        "reg_end": "2026-10-15T23:59",
    }
    # 缺项平台自己数，合成一条放最前面；联络群二维码永远要自己传
    assert d["notes"] == ["还需要自己补上：报名开始时间、联络群二维码", "原文没写报名开始时间"]
    # 发出去的请求：key 在头里，要求 json 输出，原文原样作为用户消息
    sent = calls[0]
    assert sent["url"] == f"{config.AI_BASE_URL}/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer sk-test"
    assert sent["body"]["response_format"] == {"type": "json_object"}
    assert sent["body"]["messages"][1]["content"] == TEXT
    assert "json" in sent["body"]["messages"][0]["content"]


def test_活动草稿_全选等于不限_缺项一条写完(client, admin, model):
    calls, replies = model
    replies.append((200, json.dumps({
        "title": "迎新志愿服务",
        "recruit_campuses": config.CAMPUSES,           # 全选 = 不限，留空
        "recruit_stages": ["本科生", "本科生", {"x": 1}],
        "contacts": {"name": "王"},                    # 该是数组的给了对象：当没有
        "notes": "不是数组",
    }, ensure_ascii=False)))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": TEXT})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["draft"] == {"title": "迎新志愿服务", "recruit_stages": ["本科生"]}
    assert d["notes"] == ["还需要自己补上：组织单位、活动地点、活动简介、联系人、"
                          "报名开始时间、报名截止时间、联络群二维码"]
    assert "不要写进简介" in calls[0]["body"]["messages"][0]["content"]


def test_场次草稿_岗位字段收拾好(client, admin, db, model):
    calls, replies = model
    aid = make_draft(client, admin)
    replies.append((200, json.dumps({"shifts": [{
        "name": "开幕式当天",
        "periods": [{"start_time": "2026-10-18 07:30", "end_time": "不知道"}],
        "location": "世纪馆北门",                      # 服务场次没有地点字段：丢掉
        # 场次上只收校区、学段；学段全选 = 不限
        "requirement_tags": {"校区": ["中关村校区"], "学段": config.STAGES, "性别": ["男"]},
        "positions": [
            {"name": "检录引导", "quota": "6", "service_task": "引导运动员检录",
             "attire": "issued", "attire_note": "",            # 发服装却没说怎么领：按不需要放
             "needs_leader": "yes",                             # 不是 true 就当 false
             "pay_optional": True, "pay_note": "",              # 说可选劳务却没写标准：按只记时长放
             "requirement_tags": {"性别": "女", "学院": ["文学院"], "校区": ["中关村校区"]}},
            {"name": "", "quota": 0, "service_task": "",
             "pay_optional": True, "pay_note": "100 元/天，活动后统一发放"},
        ]}], "notes": []}, ensure_ascii=False)))
    r = client.post(f"/api/staff/ai/activities/{aid}/structure-draft", headers=admin,
                    data={"text": TEXT})
    assert r.status_code == 200, r.text
    g = r.json()["shifts"][0]
    assert g["name"] == "开幕式当天"
    # 开始认出来了、结束没认出来：留半截让人补，不替它猜钟点
    assert g["periods"] == [{"start_time": "2026-10-18T07:30", "end_time": ""}]
    assert "location" not in g, "服务场次不带地点：地点在岗位与活动上"
    assert g["requirement_tags"] == {"校区": ["中关村校区"]} and g["attached"] == []
    p1, p2 = g["positions"]
    assert p1["quota"] == 6 and p1["attire"] == "none" and p1["needs_leader"] is False
    assert p1["pay_optional"] is False and p1["pay_note"] == ""
    # 岗位上只收性别：校区、学段手建时也只能设在场次上
    assert p1["requirement_tags"] == {"性别": ["女"]}
    assert p2["name"] == "志愿者" and p2["quota"] == 1 and p2["service_task"] == ""
    assert p2["pay_optional"] is True and p2["pay_note"] == "100 元/天，活动后统一发放"
    assert r.json()["notes"] == ["还需要自己补上：「开幕式当天」的时段起止时间、岗位「志愿者」的服务任务"]
    # 活动信息与已有场次带给了模型
    title = db.get(Activity, aid).title
    assert title in calls[0]["body"]["messages"][0]["content"]
    assert "上午场" in calls[0]["body"]["messages"][0]["content"]


def test_场次草稿_组长任务跟组长一起读(client, admin, db, model):
    """要组长就要写任务，写不出任务就当没要组长——落库时后端会拦。"""
    calls, replies = model
    aid = make_draft(client, admin)
    replies.append((200, json.dumps({"shifts": [{
        "name": "开幕式当天",
        "periods": [{"start_time": "2026-10-18 07:30", "end_time": "不知道"}],
        "positions": [
            {"name": "带队", "quota": 3, "service_task": "带队入场",
             "needs_leader": True, "leader_task": "带队进场、清点人数、结束后对账"},
            {"name": "引导", "quota": 3, "service_task": "引导入座",
             "needs_leader": True},                        # 要组长却没写任务：按不需要组长放
        ]}], "notes": []}, ensure_ascii=False)))
    r = client.post(f"/api/staff/ai/activities/{aid}/structure-draft", headers=admin,
                    data={"text": TEXT})
    assert r.status_code == 200, r.text
    p1, p2 = r.json()["shifts"][0]["positions"]
    assert p1["needs_leader"] is True
    assert p1["leader_task"] == "带队进场、清点人数、结束后对账"
    assert p2["needs_leader"] is False and p2["leader_task"] == ""


def test_场次草稿_培训彩排挂在场次下(client, admin, model):
    _calls, replies = model
    aid = make_draft(client, admin)
    replies.append((200, json.dumps({"shifts": [{
        "name": "比赛日",
        "periods": [{"start_time": "2026-10-25 07:00", "end_time": "2026-10-25 12:00"}],
        "positions": [{"name": "补给站", "quota": 8, "service_task": "发水"},
                      {"name": "终点引导", "quota": 4, "service_task": "引导完赛选手"}],
        "attached": [
            {"kind": "rehearsal", "name": "终点走台",
             "periods": [{"start_time": "2026-10-24 16:00",
                          "end_time": "2026-10-24 17:00"}],
             "positions": ["终点引导", "不存在的岗"],
             "hours_mode": "none", "location": "终点拱门"},
            {"kind": "meeting", "name": "",
             "positions": ["查无此岗"], "hours_mode": "weird"},
        ]}], "notes": []}, ensure_ascii=False)))
    r = client.post(f"/api/staff/ai/activities/{aid}/structure-draft", headers=admin,
                    data={"text": TEXT})
    assert r.status_code == 200, r.text
    a1, a2 = r.json()["shifts"][0]["attached"]
    assert a1 == {"kind": "rehearsal", "name": "终点走台",
                  "periods": [{"start_time": "2026-10-24T16:00",
                               "end_time": "2026-10-24T17:00"}],
                  "location": "终点拱门", "note": "",
                  "positions": ["终点引导"], "hours_mode": "none"}
    assert (a2["kind"], a2["name"], a2["positions"], a2["hours_mode"]) == \
        ("training", "集中培训", [], "fixed")
    # 点了岗位却一个没对上：按「全场次」放了，必须说出来
    assert r.json()["notes"] == ["还需要自己补上：「集中培训」的服务时段",
                                 "「集中培训」要参加的岗位没对上，先按本场次所有人放，请核对"]


def test_场次草稿_结构不对也不报错(client, admin, model):
    _calls, replies = model
    aid = make_draft(client, admin)
    replies.append((200, json.dumps({"shifts": {"name": "不是数组"}})))
    replies.append((200, json.dumps({"shifts": [{"name": "A", "positions": "x",
                                                 "attached": {"k": 1}}]})))
    url = f"/api/staff/ai/activities/{aid}/structure-draft"
    r = client.post(url, headers=admin, data={"text": TEXT})
    assert r.status_code == 200 and r.json()["shifts"] == []
    r = client.post(url, headers=admin, data={"text": TEXT})
    g = r.json()["shifts"][0]
    assert g["positions"] == [] and g["attached"] == []


def test_问卷草稿_不写原文也行_岗位名对回岗位(client, admin, db, model):
    calls, replies = model
    aid = make_draft(client, admin)
    act = db.get(Activity, aid)
    first = next(p for g in act.main_shifts() for p in g.positions)       # 上午场「引导」
    g2 = client.post(f"/api/staff/activities/{aid}/shifts", headers=admin,
                     json={"name": "下午场", "mode": "pick",
                           "periods": [{"start_time": "2026-12-01 13:00",
                                        "end_time": "2026-12-01 17:00"}]}).json()["id"]
    second = client.post(f"/api/staff/shifts/{g2}/positions", headers=admin,
                         json={"name": "引导", "quota": 2, **POSITION_TEXT}).json()["id"]
    trans = client.post(f"/api/staff/shifts/{g2}/positions", headers=admin,
                        json={"name": "翻译", "quota": 1, **POSITION_TEXT}).json()["id"]
    replies.append((200, json.dumps({"questions": [
        {"kind": "single", "title": "参加过类似活动吗", "options": ["是", "否", "是"],
         "required": True, "positions": []},
        {"kind": "multi", "title": "会哪些外语", "options": ["英语"], "positions": ["翻译"]},
        {"kind": "file", "title": "上传简历", "options": ["x"], "required": False,
         "positions": ["引导", "不存在的岗"]},
        {"kind": "essay", "title": "简单介绍一下自己"},
        {"title": ""}, "不是对象",
    ], "notes": ["要不要收简历你定"]}, ensure_ascii=False)))
    r = client.post(f"/api/staff/ai/activities/{aid}/form-draft", headers=admin,
                    data={"text": ""})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["notes"] == ["要不要收简历你定"]
    q1, q2, q3, q4 = d["questions"]
    assert (q1["kind"], q1["options"], q1["required"], q1["applies_to_positions"]) == \
        ("single", ["是", "否"], True, [])
    # 选项凑不齐两个的选择题当填空放；同名岗位（两个场次的「引导」）一起算
    assert (q2["kind"], q2["options"], q2["applies_to_positions"]) == ("text", [], [trans])
    assert (q3["kind"], q3["options"], q3["required"]) == ("file", [], False)
    assert sorted(q3["applies_to_positions"]) == sorted([first.id, second])
    assert q4["kind"] == "text"
    prompt = calls[0]["body"]["messages"][0]["content"]
    assert "报名即录取" in prompt and "- 翻译（下午场，1 人）" in prompt
    assert "不要再出题问" in prompt
    # 草稿原样就能保存：前端把它加进题目列表，管理者点保存走的是这个接口
    r = client.put(f"/api/staff/activities/{aid}/form", headers=admin, json={"questions": [
        {"kind": q["kind"], "title": q["title"], "options": q["options"],
         "required": q["required"], "applies_to_positions": q["applies_to_positions"]}
        for q in d["questions"]]})
    assert r.status_code == 200, r.text


def test_问卷草稿_补记录与已公开的不行(client, admin, db, model):
    aid = make_draft(client, admin)
    url = f"/api/staff/ai/activities/{aid}/form-draft"
    act = db.get(Activity, aid)
    act.status = "published"
    db.commit()
    r = client.post(url, headers=admin, data={"text": ""})
    assert r.status_code == 409 and "已公开" in r.json()["detail"]
    act.status, act.recruit_mode = "draft", config.RECORD_MODE
    db.commit()
    r = client.post(url, headers=admin, data={"text": ""})
    assert r.status_code == 409 and "补记录" in r.json()["detail"]


def test_原文长短与限频(client, admin, model, monkeypatch):
    calls, replies = model
    assert client.post("/api/staff/ai/activity-draft", headers=admin,
                       data={"text": "太短"}).status_code == 400
    r = client.post("/api/staff/ai/activity-draft", headers=admin,
                    data={"text": "长" * (config.AI_INPUT_MAX + 1)})
    assert r.status_code == 400 and "太长" in r.json()["detail"]
    monkeypatch.setattr(locks, "rate_count", lambda key: config.AI_HOURLY_LIMIT)
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": TEXT})
    assert r.status_code == 429
    assert not calls                     # 都在打到模型之前拦下


@pytest.mark.parametrize("status, content, word", [
    (401, "", "凭证"), (402, "", "余额"), (500, "", "暂时不可用"),
    (200, "不是 json", "不完整"), (200, "[1, 2]", "不完整"),
])
def test_模型出错给人看得懂的话(client, admin, model, status, content, word):
    _calls, replies = model
    replies.append((status, content))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": TEXT})
    assert r.status_code == 502 and word in r.json()["detail"]


def test_超时与连不上(client, admin, model, monkeypatch):
    def boom(exc):
        def _post(*a, **k):
            raise exc
        return _post
    monkeypatch.setattr(ai.httpx, "post", boom(httpx.ReadTimeout("slow")))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": TEXT})
    assert r.status_code == 502 and "太久" in r.json()["detail"]
    monkeypatch.setattr(ai.httpx, "post", boom(httpx.ConnectError("down")))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": TEXT})
    assert r.status_code == 502 and "无法连接" in r.json()["detail"]


# ======================= 附件 =======================

def _png() -> bytes:
    """一张 1×1 的 PNG（python-docx 插图要读得出尺寸，得是真 PNG）。"""
    import struct
    import zlib

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    raw = zlib.compress(b"\x00\xff\x00\x00")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def _docx() -> bytes:
    from io import BytesIO
    from docx import Document

    doc = Document()
    doc.add_paragraph("校运动会志愿者排班")
    t = doc.add_table(rows=2, cols=3)
    for i, v in enumerate(["场次", "岗位", "人数"]):
        t.cell(0, i).text = v
    for i, v in enumerate(["上午场", "检录", "6"]):
        t.cell(1, i).text = v
    doc.add_picture(BytesIO(_png()))
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _xlsx() -> bytes:
    from io import BytesIO
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "排班"
    ws.append(["场次", "岗位", "人数"])
    ws.append(["下午场", "引导", 4])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf_text(line: bytes) -> bytes:
    """一页带文字层的 PDF：按字节偏移拼好交叉引用表，正规的 PDF 阅读器都读得了。"""
    stream = b"BT /F1 18 Tf 20 100 Td (" + line + b") Tj ET"
    objs = [b"<</Type/Catalog/Pages 2 0 R>>",
            b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R"
            b"/Resources<</Font<</F1 5 0 R>>>>>>",
            b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream",
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj" % i + o + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def _blank_pdf() -> bytes:
    from io import BytesIO
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = BytesIO()
    w.write(buf)
    return buf.getvalue()


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_附件转成文字_图片按多模态发_思考默认关(client, admin, model):
    calls, replies = model
    replies.append((200, json.dumps({"title": "校运动会"}, ensure_ascii=False)))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": ""},
                    files=[("files", ("排班.docx", _docx(), DOCX)),
                           ("files", ("排班.xlsx", _xlsx(), "application/octet-stream")),
                           ("files", ("说明.pdf", _pdf_text(b"Gate A 09:00 x6"), "application/pdf")),
                           ("files", ("截图.png", _png(), "image/png"))])
    assert r.status_code == 200, r.text
    body = calls[0]["body"]
    assert body["thinking"] == {"type": "disabled"}
    parts = body["messages"][1]["content"]
    text = parts[0]["text"]
    assert "【附件：排班.docx】" in text and "上午场 | 检录 | 6" in text
    assert "【工作表：排班】" in text and "下午场 | 引导 | 4" in text
    assert "Gate A 09:00 x6" in text
    images = [p for p in parts if p["type"] == "image_url"]
    assert len(images) == 2, "docx 里插的图和单独传的截图都发了"
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_附件格式不对说清楚(client, admin, model):
    for name, data, word in [("旧版.doc", b"xx", "另存为"), ("视频.mp4", b"xx", "读不了"),
                             ("扫描.pdf", _blank_pdf(), "扫描件"), ("坏的.docx", b"xx", "损坏")]:
        r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": TEXT},
                        files=[("files", (name, data, "application/octet-stream"))])
        assert r.status_code == 400 and word in r.json()["detail"], (name, r.text)
    too_many = [("files", (f"{i}.png", _png(), "image/png")) for i in range(6)]
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": TEXT},
                    files=too_many)
    assert r.status_code == 400 and "最多传" in r.json()["detail"]


def test_只传图片也能起草(client, admin, model):
    _calls, replies = model
    replies.append((200, json.dumps({"title": "x"})))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": ""},
                    files=[("files", ("通知.jpg", _png(), "image/jpeg"))])
    assert r.status_code == 200, r.text


def test_文档里的超大图先看大小再读(client, admin, model, monkeypatch):
    """docx 是压缩包：文档只有 10MB，里面塞的图能解出几百 MB。解压之前先看
    解压后的大小，超限的图连读都不读；文档正文照常转文字。"""
    monkeypatch.setattr(config, "AI_IMAGE_MAX_BYTES", 50)    # 1×1 的 PNG 也超
    calls, replies = model
    replies.append((200, json.dumps({"title": "x"})))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": ""},
                    files=[("files", ("排班.docx", _docx(), DOCX)),
                           ("files", ("截图.png", _png(), "image/png"))])
    assert r.status_code == 200, r.text
    content = calls[0]["body"]["messages"][1]["content"]
    assert isinstance(content, str), "一张图都不该发，就是纯文本"
    assert "上午场 | 检录 | 6" in content
    assert any("太大" in n for n in r.json()["notes"])


def _bloated(doc: bytes, entry: str, extra: int) -> bytes:
    """把压缩包里的 entry 换成同一份内容外加 extra 个空格（XML 里的空白不影响解析）。
    重复字符压缩比上千倍：文件只有几十 KB，解压后却有 extra 那么大。"""
    import zipfile
    from io import BytesIO

    src = zipfile.ZipFile(BytesIO(doc))
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            body = src.read(info)
            if info.filename == entry:
                body += b" " * extra
            z.writestr(info.filename, body)
    return buf.getvalue()


def test_正文解压炸弹打开之前就挡掉(client, admin, model):
    """docx 是压缩包，正文 XML 也能塞成解压炸弹：几十 KB 的文件解出 20MB 空白。
    还没交给 python-docx 就按登记的大小和压缩比挡掉，模型一次也不调。"""
    calls, _replies = model
    bomb = _bloated(_docx(), "word/document.xml", 20 * 1024 * 1024)
    assert len(bomb) < 1024 * 1024
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": TEXT},
                    files=[("files", ("排班.docx", bomb, DOCX))])
    assert r.status_code == 400 and "解压后太大" in r.json()["detail"], r.text
    assert not calls


@pytest.mark.parametrize("limit", ["AI_ZIP_ENTRY_MAX_BYTES", "AI_ZIP_TOTAL_MAX_BYTES"])
def test_压缩包单项与总量上限(client, admin, model, monkeypatch, limit):
    """压缩比正常、但解压后单项或总量超了的，同样挡掉；xlsx 和 docx 一样查。"""
    calls, _replies = model
    monkeypatch.setattr(config, limit, 1024)
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": TEXT},
                    files=[("files", ("排班.xlsx", _bloated(_xlsx(), "xl/worksheets/sheet1.xml",
                                                          4096), "application/octet-stream"))])
    assert r.status_code == 400 and "解压后太大" in r.json()["detail"], r.text
    assert not calls


def test_正常文档不受解压上限影响(client, admin, model):
    """解压后不到 10MB 的正文不看压缩比：2MB 空白压得再狠也照常转文字。"""
    calls, replies = model
    replies.append((200, json.dumps({"title": "x"})))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": ""},
                    files=[("files", ("排班.docx", _bloated(_docx(), "word/document.xml",
                                                          2 * 1024 * 1024), DOCX)),
                           ("files", ("排班.xlsx", _xlsx(), "application/octet-stream"))])
    assert r.status_code == 200, r.text
    text = calls[0]["body"]["messages"][1]["content"][0]["text"]
    assert "上午场 | 检录 | 6" in text and "下午场 | 引导 | 4" in text


def test_pdf_页数上限(client, admin, model, monkeypatch):
    from io import BytesIO
    from pypdf import PdfWriter

    calls, _replies = model
    monkeypatch.setattr(config, "AI_PDF_MAX_PAGES", 2)
    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=200, height=200)
    buf = BytesIO()
    w.write(buf)
    r = client.post("/api/staff/ai/activity-draft", headers=admin, data={"text": TEXT},
                    files=[("files", ("长文.pdf", buf.getvalue(), "application/pdf"))])
    assert r.status_code == 400 and "超过 2 页" in r.json()["detail"], r.text
    assert not calls
