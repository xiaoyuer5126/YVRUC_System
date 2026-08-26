"""企业微信适配：模板消息推送。

模板库对应《设计方案》四.3「消息模板库」五个场景：
  报名成功 / 候补转正 / 岗位分配 / 活动变更警告 / 信用扣分提醒

MockWecom：演示模式，把消息落库（notifications 表），前端「消息中心」即收件箱。
HttpWecom：生产实现占位——学校提供 corp_id/agent_id/secret 后，
           在 _post() 补全 access_token 获取与 message/send 调用即可。
"""
from __future__ import annotations

import json
import logging
import urllib.request

from sqlalchemy.orm import Session as OrmSession

from ..models import Notification, User, uid
from . import get_config, is_mock

log = logging.getLogger(__name__)

# 模板库：key → (标题模板, 正文模板)。{xx} 为占位符。
TEMPLATES: dict[str, tuple[str, str]] = {
    "signup_success": (
        "报名成功｜{activity}",
        "您已成功报名「{activity}」· {group}。\n时间：{time}\n{detail}"
        "\n\n如无法参加，请提前24小时在平台退选，临近退选将扣除信用分。"),
    "waitlist_queued": (
        "已加入候补｜{activity}",
        "「{activity}」· {group} 名额已满，您已加入候补队列，当前第 {pos} 位。"
        "\n有名额释放时将按顺序通知您确认。"),
    "waitlist_promoted": (
        "候补转正待确认｜{activity}",
        "「{activity}」· {group} 有名额释放，您已按候补顺序转正！\n"
        "请在 {deadline} 前点击确认，超时未确认名额将顺延下一位"
        "并记一次“放弃候补”（连续3次暂停本学期候补资格）。"),
    "assignment": (
        "岗位分配通知｜{activity}",
        "您在「{activity}」· {group} 的岗位已确定：\n"
        "工位：{station}\n时间：{station_time}\n地点：{station_loc}\n请按时到岗签到。"),
    "activity_changed": (
        "活动变更提醒｜{activity}",
        "「{activity}」信息变更：\n{change_summary}\n\n请留意最新安排。"),
    "credit_penalty": (
        "信用分变动提醒",
        "您的信用分 {delta:+d} 分（当前 {score} 分）。\n事由：{reason}{extra}\n"
        "请珍惜志愿服务记录，合理安排时间。"),
    "checkin_reminder": (
        "签到提醒｜{activity}",
        "您报名的「{activity}」· {group} 即将开始，请前往 {loc} 签到到岗。"),
    "custom": (
        "{title}",
        "{content}"),
}


def render(template_key: str, extra: str = "", **kw) -> tuple[str, str]:
    """按模板库渲染标题与正文；缺占位符时保留原样，不让通知发不出去。"""
    title_tpl, body_tpl = TEMPLATES.get(template_key, ("平台通知", "{extra}"))
    kw = {**kw, "extra": extra}

    class _Safe(dict):
        def __missing__(self, k):   # 缺参时原样占位，便于发现模板问题
            return "{" + k + "}"

    return title_tpl.format_map(_Safe(kw)), body_tpl.format_map(_Safe(kw))


def send_user(db: OrmSession, user: User, template_key: str,
              data: dict | None = None, **fields) -> None:
    """向单个用户发模板消息，并把送达记录写进调用方的事务。

    消息与触发它的业务变更同生共死：业务回滚了，通知也不该留下。
    真实推送失败只标记 delivered=False，不打断业务。
    """
    title, content = render(template_key, **fields)
    delivered = True
    if not is_mock("wecom_work"):
        try:
            delivered = _http_send(user.student_id, title, content)
        except Exception:  # noqa: BLE001  外部接口不可用不能拖垮业务
            log.warning("企业微信推送失败：%s → %s", user.student_id, title,
                        exc_info=True)
            delivered = False
    db.add(Notification(id=uid("ntf"), user_id=user.id,
                        template_key=template_key, title=title,
                        content=content, data=data or {}, delivered=delivered))


def _http_send(student_id: str, title: str, content: str) -> bool:
    """真实企业微信应用消息推送（占位实现）。

    生产步骤：
      1) GET /gettoken?corpid=&corpsecret= → access_token（需缓存 ~7200s）
      2) POST /message/send  touser 用学号映射的企业微信账号
    学校开通后把两步补全即可，调用方无需改动。
    """
    cfg = get_config("wecom_work")
    body = json.dumps({
        "touser": student_id, "msgtype": "text",
        "agentid": cfg.get("agent_id"), "text": {"content": f"{title}\n{content}"},
    }).encode()
    req = urllib.request.Request(  # noqa: S310
        f"{cfg.get('base_url', 'https://qyapi.weixin.qq.com/cgi-bin')}/message/send",
        data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
        return resp.status == 200
