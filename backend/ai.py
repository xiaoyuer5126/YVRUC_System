"""智能起草（预览版）：活动基本信息、场次与岗位。

只起草、不落库。草稿回到前端：基本信息填进「新建活动」表单，场次岗位放进
可改的预览；管理者核对、改好之后，照常走新建活动 / 新建场次 / 新建岗位的
接口——校验、审计、通知都和手填的一样，平台不替人保存任何东西。

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
from .models import Activity, now, parse_local_dt

logger = logging.getLogger(__name__)

WEEKDAYS = "一二三四五六日"


class AiError(Exception):
    """起草失败，消息直接给管理者看。"""


def enabled() -> bool:
    return bool(config.AI_API_KEY)


def _chat(system: str, user: str) -> dict:
    """问一次模型，要回一个 JSON 对象。"""
    started = time.monotonic()
    try:
        r = httpx.post(
            f"{config.AI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.AI_API_KEY}"},
            json={"model": config.AI_MODEL, "temperature": 0.2,
                  "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}]},
            timeout=config.AI_TIMEOUT)
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


def _when(v) -> str:
    """模型给的时间 → 「YYYY-MM-DDTHH:MM」；认不出给空串，前端会标出来要人填。"""
    if not isinstance(v, str) or not v.strip():
        return ""
    try:
        return parse_local_dt(v).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return ""


def _pick(v, allowed: list[str]) -> list[str]:
    """多选值只留在可选集合里的：这些值要和志愿者档案逐字比对，错一个字就永远匹配不上。"""
    if not isinstance(v, list):
        return []
    return [x for x in dict.fromkeys(v) if isinstance(x, str) and x in allowed]


def _notes(v) -> list[str]:
    if not isinstance(v, list):
        return []
    return [n for n in (_text(x, 200) for x in v[:8]) if n]


def _today() -> str:
    t = now()
    return f"{t:%Y-%m-%d}（星期{WEEKDAYS[t.weekday()]}）"


# ======================= 活动基本信息 =======================

ACTIVITY_PROMPT = """你帮中国人民大学青年志愿者协会的志愿服务管理平台起草「新建活动」表单。
用户会贴来活动通知、立项书或一段口头描述。只根据原文提取，原文没有的一律留空字符串或空数组，
不要编造——尤其不要编联系人姓名和电话。今天是 {today}，原文里的「下周六」「本月 20 日」按今天推算，
时间一律写成「YYYY-MM-DD HH:MM」（北京时间）。

只输出一个 json 对象，键如下（都要有）：
{{
  "title": "活动名称，如「校运动会开幕式志愿服务」",
  "organizer": "组织单位（主办方），如「校团委」",
  "location": "活动地点，尽量具体到校区和楼",
  "intro": "活动简介：背景、服务对象、做什么，100～300 字，用原文的意思写成通顺的一段",
  "volunteer_requirement": "整个活动共通的志愿者要求（着装、集合时间等），没有就空",
  "volunteer_benefit": "志愿者保障（时长认定、餐食、纪念品、保险等），没有就空",
  "contacts": [{{"name": "联系人姓名", "phone": "电话或微信"}}],
  "recruit_mode": "instant 或 screened：报名即录取写 instant，要面试/筛选/简历的写 screened，看不出来就空",
  "recruit_campuses": ["只能从这些里选：{campuses}"],
  "recruit_stages": ["只能从这些里选：{stages}"],
  "reg_start": "报名开始时间，没写就空",
  "reg_end": "报名截止时间，没写就空",
  "notes": ["原文里没说清、需要管理者自己确认的事，每条一句，最多 5 条"]
}}
场次、岗位、人数这些不属于这张表，不用写（会在下一步单独设置），但可以在 notes 里提一句原文有没有。"""


def activity_draft(text: str) -> dict:
    data = _chat(ACTIVITY_PROMPT.format(today=_today(),
                                        campuses="、".join(config.CAMPUSES),
                                        stages="、".join(config.STAGES)),
                 text)
    draft = {
        "title": _text(data.get("title"), 128),
        "organizer": _text(data.get("organizer"), 64),
        "location": _text(data.get("location"), 128),
        "intro": _text(data.get("intro"), 2000),
        "volunteer_requirement": _text(data.get("volunteer_requirement"), 1000),
        "volunteer_benefit": _text(data.get("volunteer_benefit"), 1000),
        "recruit_campuses": _pick(data.get("recruit_campuses"), config.CAMPUSES),
        "recruit_stages": _pick(data.get("recruit_stages"), config.STAGES),
        "reg_start": _when(data.get("reg_start")),
        "reg_end": _when(data.get("reg_end")),
    }
    mode = data.get("recruit_mode")
    if mode in ("instant", "screened"):
        draft["recruit_mode"] = mode
    contacts = []
    for c in data.get("contacts") or []:
        if isinstance(c, dict):
            name, phone = _text(c.get("name"), 32), _text(c.get("phone"), 64)
            if name or phone:
                contacts.append({"name": name, "phone": phone})
    if contacts:
        draft["contacts"] = contacts[:5]
    # 空值不回：前端拿草稿去盖空表单，空串会把默认值（比如志愿者保障那句）冲掉
    return {"draft": {k: v for k, v in draft.items() if v},
            "notes": _notes(data.get("notes"))}


# ======================= 场次与岗位 =======================

STRUCTURE_PROMPT = """你帮中国人民大学青年志愿者协会的志愿服务管理平台起草一场活动的「场次」和「岗位」。
场次 = 一段服务时间（如「上午场 9:00-12:00」）；岗位 = 这段时间里的一种分工，带名额（如「检录引导 6 人」）。
同一时间段的不同分工是同一个场次下的多个岗位，不要拆成多个场次。
只根据原文提取，原文没有的留空字符串，不要编造。今天是 {today}；时间写成「YYYY-MM-DD HH:MM」（北京时间），
原文只说「当天」的，用下面活动信息里能推出的日期。

活动信息：
{activity}

只输出一个 json 对象：
{{
  "shifts": [{{
    "name": "场次名，如「开幕式当天」「第一天上午」",
    "start_time": "开始时间",
    "end_time": "结束时间",
    "location": "这个场次的地点，和活动地点一样就空",
    "positions": [{{
      "name": "岗位名",
      "quota": 名额（正整数，原文没写就按分工合理估一个，并在 notes 里说明是估的）,
      "group_name": "岗位组（几个岗位同属一组时写，如「场内组」），没有就空",
      "location": "岗位地点，和场次/活动地点一样就空",
      "service_task": "服务任务：到岗做什么，一两句话，必须写",
      "position_requirement": "岗位特有的要求（身高、外语、经验等），没有就空",
      "training_note": "培训或集合安排，没有就空",
      "needs_leader": true 或 false（人多、需要有人带队时 true）,
      "attire": "none / issued / own：不需要统一服装、统一发放、志愿者自备",
      "attire_note": "服装说明：发放的写领取和归还方式，自备的写样式；attire 为 none 时空",
      "requirement_tags": {{"性别": ["男" 或 "女"], "校区": ["只能从这些里选：{campuses}"], "学段": ["只能从这些里选：{stages}"]}}
    }}]
  }}],
  "notes": ["原文里没说清、估算的、需要管理者确认的事，每条一句，最多 5 条"]
}}
requirement_tags 只在原文明确限定时才写，不限就给空对象 {{}}。培训、彩排不要算成场次，写进 training_note。"""

ATTIRES = ("none", "issued", "own")


def _activity_brief(act: Activity) -> str:
    lines = [f"名称：{act.title}", f"组织单位：{act.organizer or '—'}",
             f"地点：{act.location or '—'}", f"简介：{(act.intro or '')[:600]}"]
    if act.reg_end:
        lines.append(f"报名截止：{act.reg_end:%Y-%m-%d %H:%M}")
    if act.main_shifts():
        lines.append("已经建好的场次（不要重复）：" + "；".join(
            f"{g.name} {g.span_text()}" for g in act.main_shifts()))
    return "\n".join(lines)


def _tags(v) -> dict:
    if not isinstance(v, dict):
        return {}
    known = {"性别": ["男", "女"], "校区": config.CAMPUSES, "学段": config.STAGES}
    out = {}
    for tag, allowed in known.items():
        vals = _pick(v.get(tag) if isinstance(v.get(tag), list) else [v.get(tag)], allowed)
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
    return {
        "name": _text(p.get("name"), 128) or "志愿者",
        "quota": quota,
        "group_name": _text(p.get("group_name"), 64),
        "location": _text(p.get("location"), 128),
        "service_task": _text(p.get("service_task"), 1000),
        "position_requirement": _text(p.get("position_requirement"), 1000),
        "training_note": _text(p.get("training_note"), 1000),
        "needs_leader": p.get("needs_leader") is True,
        "attire": attire, "attire_note": attire_note,
        "requirement_tags": _tags(p.get("requirement_tags")),
    }


def structure_draft(act: Activity, text: str) -> dict:
    data = _chat(STRUCTURE_PROMPT.format(today=_today(), activity=_activity_brief(act),
                                         campuses="、".join(config.CAMPUSES),
                                         stages="、".join(config.STAGES)),
                 text)
    shifts = []
    for g in (data.get("shifts") or [])[:20]:
        if not isinstance(g, dict):
            continue
        positions = [_position(p) for p in (g.get("positions") or [])[:30]
                     if isinstance(p, dict)]
        shifts.append({
            "name": _text(g.get("name"), 128) or f"场次 {len(shifts) + 1}",
            "start_time": _when(g.get("start_time")),
            "end_time": _when(g.get("end_time")),
            "location": _text(g.get("location"), 128),
            "positions": positions,
        })
    return {"shifts": shifts, "notes": _notes(data.get("notes"))}
