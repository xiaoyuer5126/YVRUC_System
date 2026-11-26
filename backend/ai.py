"""智能起草（预览版）：活动基本信息、岗位（自带时段）、报名问卷。

只起草、不落库。草稿回到前端：基本信息填进「新建活动」表单，岗位放进
可改的预览，问卷题目加进出题列表；管理者核对、改好之后，照常走新建活动 /
新建岗位 / 新建培训彩排 / 保存报名问卷的接口——校验、审计、通知都和手填的一样，
平台不替人保存任何东西。

缺了哪些必填信息由平台对着草稿自己数，合成一条放在提醒的最前面：模型数不准，
也不该占它那几条提醒的名额。

模型说的话一律当不可信输入：字段按白名单逐个取，按类型、长度、可选值收，
认不得的丢掉，不往外透传。拿不准的让模型写进 notes，原样给管理者看。

接口是 OpenAI 兼容的 /chat/completions（DeepSeek 即此类），参数见 config.AI_*。
"""
from __future__ import annotations

import json
import logging
import time

import httpx

from . import config
from .models import Activity, FormQuestion, now, parse_local_dt
from .serializers import recruit_scope_text

logger = logging.getLogger(__name__)

WEEKDAYS = "一二三四五六日"


class AiError(Exception):
    """起草失败，消息直接给管理者看。"""


def enabled() -> bool:
    return bool(config.AI_API_KEY)


def _chat(system: str, user: str, images: list[str] = ()) -> dict:
    """问一次模型，要回一个 JSON 对象。images：附件里的图片（data: URL），
    和原文一起作为多模态消息发过去。"""
    content: str | list = user
    if images:
        content = ([{"type": "text", "text": f"{user}\n\n另附图片 {len(images)} 张，"
                                              "图里的通知、排班、分工也要读。"}]
                   + [{"type": "image_url", "image_url": {"url": u}} for u in images])
    body = {"model": config.AI_MODEL, "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": content}]}
    if config.AI_THINKING in ("enabled", "disabled"):
        body["thinking"] = {"type": config.AI_THINKING}
    started = time.monotonic()
    try:
        r = httpx.post(
            f"{config.AI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.AI_API_KEY}"},
            json=body, timeout=config.AI_TIMEOUT)
    except httpx.TimeoutException:
        raise AiError("AI 这次想得太久了，请把原文删短一些再试")
    except httpx.HTTPError:
        raise AiError("AI 服务无法连接，请稍后再试")
    # 只记耗时和状态，不记原文：原文里可能有联系人电话
    logger.info("智能起草 %s %.1fs", r.status_code, time.monotonic() - started)
    if r.status_code in (401, 403):
        raise AiError("智能起草服务凭证错误，请联系管理员")
    if r.status_code == 402:
        raise AiError("智能起草服务余额不足，请联系管理员")
    if r.status_code == 429:
        raise AiError("智能起草服务繁忙，请过会儿再试")
    if r.status_code != 200:
        raise AiError("智能起草服务暂时不可用，请稍后再试")
    try:
        content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError):
        raise AiError("AI 返回的内容不完整，请再试一次")
    if not isinstance(data, dict):
        raise AiError("AI 返回的内容不完整，请再试一次")
    return data


# ======================= 收拾模型的输出 =======================

def _text(v, limit: int) -> str:
    return v.strip()[:limit] if isinstance(v, str) else ""


def _list(v) -> list:
    """该是数组的地方给了别的（对象、字符串），当没有。"""
    return v if isinstance(v, list) else []


def _strs(v) -> list[str]:
    return [x for x in _list(v) if isinstance(x, str)]


def _when(v) -> str:
    """模型给的时间 → 「YYYY-MM-DDTHH:MM」；认不出给空串，前端会标出来要人填。"""
    if not isinstance(v, str) or not v.strip():
        return ""
    try:
        return parse_local_dt(v).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return ""


def _periods(v) -> list[dict]:
    """模型给的时段：起止都认出来才算数，只认出开始的留着半截让人补。"""
    out = []
    for p in _list(v)[:60]:
        if not isinstance(p, dict):
            continue
        start, end = _when(p.get("start_time")), _when(p.get("end_time"))
        if not start:
            continue
        if end and end <= start:
            end = ""
        out.append({"start_time": start, "end_time": end})
    out.sort(key=lambda x: x["start_time"])
    return out


def _pick(v, allowed: list[str]) -> list[str]:
    """多选值只留在可选集合里的：这些值要和志愿者档案逐字比对，错一个字就永远匹配不上。"""
    return [x for x in dict.fromkeys(_strs(v)) if x in allowed]


def _scope(v, allowed: list[str]) -> list[str]:
    """招募范围、岗位条件：全选等于不限，留空——不然表单里一排全勾上，看着像有限制。"""
    picked = _pick(v, allowed)
    return [] if set(picked) == set(allowed) else picked


def _notes(v, missing: list[str] | None = None) -> list[str]:
    """模型的提醒，前面加上平台数出来的缺项（一条写完）。"""
    notes = [n for n in (_text(x, 200) for x in _list(v)[:8]) if n]
    return ([f"还需要自己补上：{'、'.join(missing)}"] if missing else []) + notes


def _today() -> str:
    t = now()
    return f"{t:%Y-%m-%d}（星期{WEEKDAYS[t.weekday()]}）"


# ======================= 活动基本信息 =======================

ACTIVITY_PROMPT = """你帮中国人民大学的志愿服务管理平台起草「新建活动」表单。
用户会贴来活动通知、立项书或一段口头描述，也可能有上传文件转出的文字（表格一行一行，
单元格用「 | 」隔开）和图片。只根据原文提取，原文没有的一律留空字符串或空数组，
不要编造——尤其不要编联系人姓名和电话。今天是 {today}，原文里的「下周六」「本月 20 日」等按今天推算，
时间一律写成「YYYY-MM-DD HH:MM」（北京时间）。

只输出一个 json 对象，键如下（都要有）：
{{
  "title": "活动名称，如「校运动会开幕式志愿服务」",
  "organizer": "组织单位（主办方），如「校团委」",
  "co_organizer": "联合主办方：与别的单位合办时才有，写另一个单位的名字；原文没提合办就空",
  "location": "活动地点，按原文提取",
  "intro": "活动简介：介绍活动背景、服务对象等，不超过 100 字，用原文的意思写成通顺的一段。时间、地点、主办方、招募范围、报名方式、联系人、保障这些表单里各有字段，岗位也有专门的表单，不要写进简介",
  "volunteer_requirement": "整个活动共通的志愿者要求，原文提取，没有就空",
  "precautions": "注意事项：提醒类的话（如穿深色衣服、自备口罩、过敏慎报），原文提取，没有就空；算报名门槛的写进志愿者要求",
  "volunteer_benefit": "志愿者保障（时长认定、餐食、纪念品、保险等），没有就空",
  "contacts": [{{"name": "联系人姓名", "phone": "联系方式"}}],
  "recruit_mode": "instant 或 screened：报名即录取写 instant，要面试/筛选/简历的写 screened，看不出来就默认instant",
  "recruit_campuses": ["只能从这些里选：{campuses}。原文明确只招某些校区才写；不限、面向全校、各校区都招的给空数组"],
  "recruit_stages": ["只能从这些里选：{stages}。原文明确只招某些学段才写；不限的给空数组"],
  "reg_start": "报名开始时间，没写就空",
  "reg_end": "报名截止时间，没写就空",
  "notes": ["原文里说得含糊、需要管理者自己拿主意的事，每条一句，最多 5 条。缺了哪些信息不用写，平台会自己列出来"]
}}
岗位、人数这些不属于这张表，不用写（会在下一步单独设置），也不用在 notes 中提到。"""


ACTIVITY_REQUIRED = [("title", "活动名称"), ("organizer", "组织单位"), ("location", "活动地点"),
                     ("intro", "活动简介"), ("contacts", "联系人"),
                     ("reg_start", "报名开始时间"), ("reg_end", "报名截止时间")]


def activity_draft(text: str, images: list[str] = ()) -> dict:
    data = _chat(ACTIVITY_PROMPT.format(today=_today(),
                                        campuses="、".join(config.CAMPUSES),
                                        stages="、".join(config.STAGES)),
                 text, images)
    draft = {
        "title": _text(data.get("title"), 128),
        "organizer": _text(data.get("organizer"), 64),
        "co_organizer": _text(data.get("co_organizer"), 64),
        "location": _text(data.get("location"), 128),
        "intro": _text(data.get("intro"), 2000),
        "volunteer_requirement": _text(data.get("volunteer_requirement"), 1000),
        "precautions": _text(data.get("precautions"), 1000),
        "volunteer_benefit": _text(data.get("volunteer_benefit"), 1000),
        "recruit_campuses": _scope(data.get("recruit_campuses"), config.CAMPUSES),
        "recruit_stages": _scope(data.get("recruit_stages"), config.STAGES),
        "reg_start": _when(data.get("reg_start")),
        "reg_end": _when(data.get("reg_end")),
    }
    mode = data.get("recruit_mode")
    if mode in ("instant", "screened"):
        draft["recruit_mode"] = mode
    contacts = []
    for c in _list(data.get("contacts")):
        if isinstance(c, dict):
            name, phone = _text(c.get("name"), 32), _text(c.get("phone"), 64)
            if name or phone:
                contacts.append({"name": name, "phone": phone})
    if contacts:
        draft["contacts"] = contacts[:5]
    # 联络群二维码永远要自己传；志愿者保障没写有默认的那句，不算缺
    missing = [label for key, label in ACTIVITY_REQUIRED if not draft.get(key)]
    # 空值不回：前端拿草稿去盖空表单，空串会把默认值（比如志愿者保障那句）冲掉
    return {"draft": {k: v for k, v in draft.items() if v},
            "notes": _notes(data.get("notes"), missing + ["联络群二维码"])}


# ======================= 岗位（自带时段） =======================

STRUCTURE_PROMPT = """你帮中国人民大学的志愿服务管理平台起草一场活动的「岗位」。
岗位 = 一种分工，带名额和自己的服务时段（如「检录引导 6 人，10-25 上午」）。
报名按岗位算：报了一个岗位的人，要在它列出的每个时段都到场。
原文说「10.25–28、10.31–11.3 都要同一批人」这类跨几个不连续日期、要求同一批人的，写成一个岗位、多个时段，
不要为了日期不连续拆成两个岗位；每天的服务时间不同的，每个时段各写各的时间。
同一份分工在不同时段换人做（上午一批、下午一批），每个时段各写一个岗位。
每个时段写起止时间「YYYY-MM-DD HH:MM」。原文只说上午 / 下午 / 全天的，按
08:00-12:00 / 13:00-18:00 / 08:00-18:00 写一个大概的钟点，管理者会自己改；
「10.25–28 上午」要写成 10-25、10-26、10-27、10-28 四段，不要合成一段。
培训、彩排不是服务岗位：原文给了具体时间的，写进 trainings（时间同样用 periods 写），用岗位的 no 写清哪些岗位要参加；
没有具体时间、只是到岗前要知道的培训内容，写进岗位的 training_note。
原文可能含上传文件转出的表格（一行一行，单元格用「 | 」隔开）和图片，排班常常就在表里。
只根据原文提取，原文没有的留空，不要编造。今天是 {today}；时间按北京时间，
原文只说「当天」的，用下面活动信息里能推出的日期。

活动信息：
{activity}

只输出一个 json 对象：
{{
  "posts": [{{
    "no": 岗位序号，从 1 起往后数，每个岗位一个,
    "name": "岗位名",
    "periods": [{{"start_time": "开始时间", "end_time": "结束时间"}}],
    "lock_hours": 开场前多少小时锁定名单（0～{lock_max} 的整数，按第一个时段算）。原文说了「提前多久确定名单 / 之后不能退出」就按原文换算成小时，如「活动前一天」写 24；没说写 {lock_default},
    "quota": 名额（正整数，原文没写就按分工合理估一个）,
    "group_name": "岗位组（几个岗位同属一组时写，如「场内组」），没有就空",
    "location": "岗位地点，和活动地点一样就空",
    "service_task": "服务任务：到岗做什么，一两句话，必须写",
    "position_requirement": "岗位特有的要求（身高、外语、经验等），没有就空",
    "training_note": "到岗前要知道的培训内容、集合安排，没有就空",
    "needs_leader": true 或 false（原文提到组长时 true；true 时必须写 leader_task）,
    "leader_task": "组长任务：组长要带队做什么，如「带队进场、清点人数」；needs_leader 为 false 时空",
    "attire": "none / issued / own：不需要统一服装、统一发放、志愿者自备",
    "attire_note": "服装说明：发放的写领取和归还方式，自备的写样式；attire 为 none 时空",
    "pay_optional": true 或 false（原文说志愿者可以选领劳务报酬的写 true，其余写 false）,
    "pay_note": "劳务报酬的标准，如「100 元/天，按具体服务时间计算」；pay_optional 为 false 时空",
    "requirement_tags": {{"性别": ["男" 或 "女"], "校区": ["只能从这些里选：{campuses}"], "学段": ["只能从这些里选：{stages}"]}}
  }}],
  "trainings": [{{
    "kind": "training 或 rehearsal：培训写 training，彩排写 rehearsal",
    "name": "如「集中培训」「开幕式彩排」",
    "periods": [{{"start_time": "开始时间", "end_time": "结束时间"}}],
    "location": "和活动地点一样就空",
    "note": "备注，没有就空",
    "posts": [需要参加的岗位的 no；所有岗位的人都要参加就给空数组],
    "hours_mode": "fixed / checkin / none：一般写 fixed（到场即按这一场的时长计）；原文说按签到签退计时写 checkin；原文说不计时长、或者是线上培训写 none"
  }}],
  "notes": ["原文里没说清、需要管理者确认的事，每条一句，最多 5 条；估了名额的岗位合成一条写。缺时间、缺服务任务不用写，平台会自己列出来"]
}}
条件只在原文明确限定时才写，不限就给空对象 {{}}，而且只在原文说「这个岗位只要某类人」时才写——
整个活动的招募范围已经在活动信息里设好了，不要重复。"""

ATTIRES = ("none", "issued", "own")
ATTACHED_KINDS = ("training", "rehearsal")
ATTACHED_HOURS = ("fixed", "checkin", "none")
# 岗位能设的条件，和手建岗位时表单上的一致（见 staff_activities.POSITION_TAGS）
POSITION_TAGS = {"性别": ["男", "女"], "校区": config.CAMPUSES,
                 "学段": config.STAGES}


def _activity_brief(act: Activity) -> str:
    lines = [f"名称：{act.title}", f"组织单位：{act.organizer_text() or '—'}",
             f"地点：{act.location or '—'}", f"招募范围：{recruit_scope_text(act)}",
             f"简介：{(act.intro or '')[:600]}"]
    if act.reg_end:
        lines.append(f"报名截止：{act.reg_end:%Y-%m-%d %H:%M}")
    if act.main_shifts():
        lines.append("已经建好的岗位（不要重复）：" + "；".join(
            (f"统一分配「{g.name}」" if g.needs_assign()
             else "、".join(p.name for p in g.real_positions()) or g.name)
            + f" {g.span_text()}" for g in act.main_shifts()))
    return "\n".join(lines)


def _tags(v, known: dict[str, list[str]]) -> dict:
    if not isinstance(v, dict):
        return {}
    out = {}
    for tag, allowed in known.items():
        raw = v.get(tag)
        vals = _scope(raw if isinstance(raw, list) else [raw], allowed)
        if vals:
            out[tag] = vals
    return out


def _position(p: dict) -> dict:
    try:
        quota = max(1, min(999, int(p.get("quota"))))
    except (TypeError, ValueError):
        quota = 1
    attire = p.get("attire") if p.get("attire") in ATTIRES else "none"
    attire_note = _text(p.get("attire_note"), 256) if attire != "none" else ""
    # 要统一服装却没说怎么领、什么样式：后端会拦，草稿里先按「不需要」放，让人自己定
    if not attire_note:
        attire = "none"
    pay = p.get("pay_optional") is True
    pay_note = _text(p.get("pay_note"), 256) if pay else ""
    # 说了可选劳务却没写标准和发放方式：后端会拦，同上先按只记时长放
    if not pay_note:
        pay = False
    leader = p.get("needs_leader") is True
    leader_task = _text(p.get("leader_task"), 256) if leader else ""
    # 说了要组长却没写任务：后端会拦，同上先按不需要组长放
    if not leader_task:
        leader = False
    return {
        "name": _text(p.get("name"), 128) or "志愿者",
        "quota": quota,
        "group_name": _text(p.get("group_name"), 64),
        "location": _text(p.get("location"), 128),
        "service_task": _text(p.get("service_task"), 1000),
        "position_requirement": _text(p.get("position_requirement"), 1000),
        "training_note": _text(p.get("training_note"), 1000),
        "needs_leader": leader, "leader_task": leader_task,
        "attire": attire, "attire_note": attire_note,
        "pay_optional": pay, "pay_note": pay_note,
        "requirement_tags": _tags(p.get("requirement_tags"), POSITION_TAGS),
    }


def _ref(v) -> str:
    """岗位序号的统一写法：模型可能写 2、2.0 或 "2"，都认成 "2"；别的给空串。"""
    if isinstance(v, bool):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip() if isinstance(v, (str, int)) else ""


def _refs(v) -> list[str]:
    return [r for r in dict.fromkeys(_ref(x) for x in _list(v)) if r]


def _attached(a: dict, nos: dict) -> dict:
    """培训/彩排。模型用草稿里岗位的序号 no 指认要参加的岗位（岗位还没建，
    没有 id；同名岗位分几个时段时按名字分不开），这里换成岗位在 posts 里的下标。"""
    kind = a.get("kind") if a.get("kind") in ATTACHED_KINDS else "training"
    return {
        "kind": kind,
        "name": _text(a.get("name"), 128) or ("集中培训" if kind == "training" else "彩排"),
        "periods": _periods(a.get("periods")),
        "location": _text(a.get("location"), 128),
        "note": _text(a.get("note"), 1000),
        "posts": [nos[n] for n in _refs(a.get("posts")) if n in nos],
        "hours_mode": (a.get("hours_mode") if a.get("hours_mode") in ATTACHED_HOURS
                       else "fixed"),
    }


def _lock_hours(v) -> int:
    """开场前多少小时锁定名单：不是 0～LOCK_HOURS_MAX 的整数就用默认值。"""
    if isinstance(v, bool):
        return config.LOCK_HOURS_DEFAULT
    try:
        n = int(v)
    except (TypeError, ValueError):
        return config.LOCK_HOURS_DEFAULT
    return n if 0 <= n <= config.LOCK_HOURS_MAX else config.LOCK_HOURS_DEFAULT


def _structure_missing(posts: list[dict], trainings: list[dict]) -> list[str]:
    """缺的时段、服务任务。岗位多了只报个数：预览里缺服务任务的已经标红。"""
    out = []
    for s in (*posts, *trainings):
        if not s["periods"]:
            out.append(f"「{s['name']}」的服务时段")
            continue
        for p in s["periods"]:
            if not (p["start_time"] and p["end_time"]):
                out.append(f"「{s['name']}」的时段起止时间")
                break
    out = list(dict.fromkeys(out))
    tasks = list(dict.fromkeys(p["name"] for p in posts if not p["service_task"]))
    if tasks:
        out.append(f"岗位「{'」「'.join(tasks)}」的服务任务" if len(tasks) <= 3
                   else f"{len(tasks)} 个岗位的服务任务")
    return out


def structure_draft(act: Activity, text: str, images: list[str] = ()) -> dict:
    data = _chat(STRUCTURE_PROMPT.format(today=_today(), activity=_activity_brief(act),
                                         campuses="、".join(config.CAMPUSES),
                                         stages="、".join(config.STAGES),
                                         lock_default=config.LOCK_HOURS_DEFAULT,
                                         lock_max=config.LOCK_HOURS_MAX),
                 text, images)
    # 岗位平铺，各带各的时段；序号 no 只用来让培训/彩排指认岗位，规整后换成下标
    posts, nos = [], {}
    for p in _list(data.get("posts"))[:60]:
        if not isinstance(p, dict):
            continue
        no = _ref(p.get("no"))
        if no and no not in nos:
            nos[no] = len(posts)
        posts.append({**_position(p), "periods": _periods(p.get("periods")),
                      "lock_hours": _lock_hours(p.get("lock_hours"))})
    trainings, lost = [], []
    for a in _list(data.get("trainings"))[:20]:
        if isinstance(a, dict):
            trainings.append(_attached(a, nos))
            # 点了岗位却一个都没对上：空着就成了「全体都要来」，得告诉人
            if _refs(a.get("posts")) and not trainings[-1]["posts"]:
                lost.append(trainings[-1]["name"])
    notes = _notes(data.get("notes"), _structure_missing(posts, trainings))
    notes += [f"「{n}」要参加的岗位没对上，先按全体参加放，请核对" for n in lost]
    return {"posts": posts, "trainings": trainings, "notes": notes}


# ======================= 报名问卷 =======================

FORM_PROMPT = """你帮中国人民大学的志愿服务管理平台给一场志愿活动出「报名问卷」的题目。
{mode}
平台的志愿者档案里已经有这些信息，导出名单时自动带上，不要再出题问：
姓名、学号、性别、学院、年级、校区、学段、手机号、邮箱、微信号、身份证号。
报名时平台还会自己问报哪个岗位、哪个时段、愿不愿意当组长，这些也不要出题；
志愿时长和以往的出勤表现，管理者在名单里能直接看到，也不用问。

活动信息：
{activity}

用户可能补充了想了解的事，按用户要求出题；没补充就按活动简介和岗位要求出几道题。
题目少而精，最多 10 道；题干简短，不超过 50 字。

只输出一个 json 对象：
{{
  "questions": [{{
    "kind": "single / multi / text / file：单选、多选、填空、文件上传（要交简历、作品、证书时才用）",
    "title": "题干",
    "options": ["单选、多选的选项，至少两个；填空和文件上传给空数组"],
    "required": true 或 false（不答就没法安排或筛选的写 true，锦上添花的写 false）,
    "positions": ["只问哪些岗位的人：写岗位名，和活动信息里的岗位名一字不差；人人都答就给空数组"]
  }}],
  "notes": ["需要管理者自己拿主意的事，每条一句，最多 3 条"]
}}"""

FORM_MODES = {
    "screened": "这场活动「报名后筛选」：志愿者先交申请和问卷，管理者看完再决定录不录。"
                "问卷是筛选的依据，问能看出合不合适的事（相关经历、技能、空闲时间等）。",
    "instant": "这场活动「报名即录取」：报上就算录取，问卷是选填的。"
               "只问组织活动真要用到的事（如服装尺码、饮食禁忌），不出筛选性质的题。",
}


def _form_brief(act: Activity) -> str:
    lines = [f"名称：{act.title}", f"组织单位：{act.organizer_text() or '—'}",
             f"简介：{(act.intro or '')[:600]}"]
    if (act.volunteer_requirement or "").strip():
        lines.append(f"志愿者要求：{act.volunteer_requirement.strip()[:300]}")
    lines.append("岗位：")
    for g in sorted(act.main_shifts(), key=lambda x: x.sort):
        for p in sorted(g.real_positions(), key=lambda x: x.sort):
            extra = "；".join(x for x in (
                (p.service_task or "").strip()[:150],
                f"岗位要求：{p.position_requirement.strip()[:150]}"
                if (p.position_requirement or "").strip() else "") if x)
            # 岗位名重名是常事（同一份活儿两个时段各招一批），带上时段才认得出
            lines.append(f"- {p.name}（{g.span_text()}，{p.quota} 人）"
                         f"{'：' + extra if extra else ''}")
    if lines[-1] == "岗位：":
        lines[-1] = "岗位：还没建"
    return "\n".join(lines)


def form_draft(act: Activity, text: str, images: list[str] = ()) -> dict:
    data = _chat(FORM_PROMPT.format(mode=FORM_MODES[act.recruit_mode],
                                    activity=_form_brief(act)),
                 text or "（没有补充，按活动信息出题）", images)
    # 同名岗位（上午场、下午场都有「检录引导」）一起算：问的是这类岗位的人
    ids_by_name: dict[str, list[str]] = {}
    for g in act.main_shifts():
        for p in g.real_positions():
            ids_by_name.setdefault(p.name, []).append(p.id)
    questions = []
    for q in _list(data.get("questions"))[:15]:
        title = _text(q.get("title"), 200) if isinstance(q, dict) else ""
        if not title:
            continue
        kind = q.get("kind") if q.get("kind") in FormQuestion.KINDS else "text"
        options = [o for o in dict.fromkeys(_text(x, 100) for x in _list(q.get("options"))[:20])
                   if o]
        # 选项凑不齐两个的选择题当填空放，免得一保存就被拦
        if kind in ("single", "multi") and len(options) < 2:
            kind = "text"
        questions.append({
            "kind": kind, "title": title,
            "options": options if kind in ("single", "multi") else [],
            "required": q.get("required") is not False,
            "applies_to_positions": [i for n in dict.fromkeys(_strs(q.get("positions")))
                                     for i in ids_by_name.get(n, [])],
        })
    return {"questions": questions, "notes": _notes(data.get("notes"))}
