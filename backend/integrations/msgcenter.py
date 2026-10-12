"""消息中心适配：模板消息推送。

模板库覆盖以下场景：
  报名成功 / 候补转正 / 岗位分配 / 活动变更警告

Mock 模式：把消息落库（notifications 表），前端「消息中心」即收件箱。
真实模式（YVRUC_MSGCENTER_ENABLED=1）：落库之外，再经学校开放平台的消息中心
（opengw）下发。

点开一条通知去哪一页，只有 page_for 这一份规则：站内消息中心按它跳，企业微信
卡片的点击链接也由它拼出来（_link_for，走 extras 的 im_linkUrl）。

**默认只发企业微信通道（channels=4）**。学校未同意本平台发短信，而短信是按条
计费的真金白银——YVRUC_MSGCENTER_CHANNELS 里出现 1，启动时 integrations.check()
就拒绝启动。
"""
from __future__ import annotations

import logging

from sqlalchemy import event, update
from sqlalchemy.orm import Session as OrmSession

from .. import config, locks, sudy_sign
from ..database import SessionLocal
from ..models import Notification, User, uid

log = logging.getLogger(__name__)

# 开放网关下的接口路径（域名为 config.OPENPLATFORM_GATEWAY）
SEND_PATH = "com.sudytech.msgcenter/ucp/_web/_apps/ucpmsg/api/v3/sendMessage.rst"

# 模板库：key → (标题模板, 正文模板)。{xx} 为占位符。
TEMPLATES: dict[str, tuple[str, str]] = {
    "signup_success": (
        "志愿活动报名成功｜{activity}",
        "您已成功报名「{activity}」· {shift}。\n时间：{time}\n{detail}"
        "\n\n如无法参加，请尽早退选；场次名单锁定后退出将被记录。"),
    "waitlist_queued": (
        "已加入候补｜{activity}",
        "「{activity}」· {shift} 名额已满，您已报名候补，当前队列第 {pos} 位。"
        "\n有名额时将通知您确认，需于转正通知2小时内确认。"),
    "hours_approved": (
        "志愿时长已核准｜{activity}",
        "「{activity}」的志愿时长已核准：您本次记 {hours} 小时。"
        "\n请于 {deadline} 前到「志愿时长」页确认或提出申诉，超时未确认则视为无误，"
        "之后修改需报审批。{pay_line}"),
    "hours_locked": (
        "志愿时长已锁定｜{activity}",
        "「{activity}」的志愿时长已锁定，{result}。"
        "\n感谢您的参与！"),
    "application_accepted": (
        "志愿活动录取通知｜{activity}",
        "恭喜！您已被「{activity}」录取，安排如下：\n{arrangement}"
        "\n请到「我的报名」查看志愿者联络群二维码，"
        "入群后请把群昵称改为「姓名+电话」。"),
    # 措辞留余地：这一次没排上不代表什么，也不留任何记录
    "application_rejected": (
        "志愿活动报名结果｜{activity}",
        "感谢您报名「{activity}」。本次名额有限，暂未能安排您参加。"
        "\n让有爱者有力，让奉献者发光，期待与同学一起参与更多丰富多彩的志愿活动。"),
    "waitlist_promoted": (
        "候补转正待确认｜{activity}",
        "「{activity}」· {shift} 有名额释放，您已按候补顺序转正！\n"
        "请在 {deadline} 前点击确认，超时未确认则名额将顺延下一位。"),
    "assignment": (
        "岗位分配通知｜{activity}",
        "您在「{activity}」· {shift} 的岗位已确定：\n"
        "岗位：{position}\n时间：{position_time}\n地点：{position_loc}\n"
        "请查看培训、小组长等信息，活动时按要求到岗签到。"),
    "activity_changed": (
        "活动变更提醒｜{activity}",
        "「{activity}」信息变更：\n{change_summary}\n\n请留意最新安排。"),
    "leader_appointed": (
        "您已成为岗位组长｜{activity}",
        "您在「{activity}」已被负责人选为岗位「{position}」组长。\n"
        "时间：{position_time}\n地点：{position_loc}\n"
        "组长任务：{leader_task}\n"
        "请提前与负责人对接现场安排。"),
    # 补招：管理者把人直接加进场次。场次还没结束是叫他来，结束了是事后补登记
    "walkin_added": (
        "您已被补招｜{activity}",
        "负责人已将您加入「{activity}」· {shift}。\n"
        "时间：{time}\n地点：{loc}\n岗位：{position}\n{attached}"
        "如无法参加，请尽快联系负责人。"),
    "walkin_recorded": (
        "已补记为志愿者｜{activity}",
        "负责人已将您补记为「{activity}」· {shift} 的志愿者。\n"
        "时间：{time}\n时长以负责人登记为准，录入后可在「我的志愿时长」查看。"),
    "checkin_reminder": (
        "志愿活动签到提醒｜{activity}",
        "您报名的「{activity}」· {shift} 即将开始。\n"
        "岗位：{position}\n地点：{loc}\n"
        "请按要求到岗并签到，服务结束后记得签退。"),
    # 催签收：本身不要签收，否则催一次就多一条待办，5 个人的批次会变成 10 条
    "ack_reminder": (
        "志愿通知待签收提醒｜{title}",
        "您有一条通知未签收：\n「{title}」\n"
        "请到消息中心点击「我已知悉」，方便统计签收情况。"),
    "custom": (
        "{title}",
        "{content}"),
}


# 各模板点开后落在哪一页（站内路径），配了就以它为准：报名/录取
# 去「我的报名」看自己的状态和联络群，时长去「我的志愿时长」——确认与申诉的
# 按钮只在那儿，带去活动页等于让人自己再找一遍。
TEMPLATE_PAGE: dict[str, str] = {
    "signup_success": "/my",
    "waitlist_queued": "/my",
    "waitlist_promoted": "/my",
    "application_accepted": "/my",
    "application_rejected": "/my",
    "assignment": "/my",
    "walkin_added": "/my",
    "walkin_recorded": "/my",
    "leader_appointed": "/my",
    "hours_approved": "/hours",
    "hours_locked": "/hours",
}

# data.route 能直接当页面路径的取值。名单外的不猜，认错页面比不跳更糟。
ROUTE_PAGES = ("hours", "approvals", "feedback")
# route="manage"：发给活动管理者的，进活动管理页；data.tab 指定直接打开哪个页签
MANAGE_TABS = ("tree", "roster", "hours", "review")


def page_for(template_key: str, data: dict | None) -> str:
    """点开这条通知该去哪一页（站内路由路径）；没有合适的页返回空串。

    站内消息中心（/api/me/notifications 里的 page）和企业微信卡片（_link_for）
    都按它跳。依次看：
    1. 模板配了落点（TEMPLATE_PAGE）；
    2. data.route 指的页。它排在活动 id 前面：待办的审批也带着活动 id，
       可审批人在活动详情页看不到通过/驳回的按钮；发给活动管理者的
       （route="manage"）进活动管理页，学生看的那个详情页上没有他要办的事；
    3. data.activity_id：进活动页；
    4. 都没有：不跳。
    """
    data = data or {}
    activity_id = data.get("activity_id")
    if template_key in TEMPLATE_PAGE:
        return TEMPLATE_PAGE[template_key]
    if data.get("route") in ROUTE_PAGES:
        return "/" + data["route"]
    if data.get("route") == "manage" and activity_id:
        tab = data.get("tab")
        return f"/manage/{activity_id}" + (f"?tab={tab}" if tab in MANAGE_TABS else "")
    if activity_id:
        return f"/activity/{activity_id}"
    return ""


def _link_for(template_key: str, data: dict | None, need_ack: bool) -> str:
    """企业微信卡片点开的完整网址；没有合适的页、或站点根没配，返回空串。

    要签收的和催签收的一律落在消息中心：「我已知悉」按钮只在那儿，带去别的
    页就签不了；签完再点这条，照样去 page_for 那一页。站内不必这样——签收
    按钮就在消息卡片上。
    站点根（config.SITE_URL）不配就不发链接：带半截路径的 im_linkUrl
    打开是白屏，不如不点。
    """
    if need_ack or template_key == "ack_reminder":
        page = "/messages"
    else:
        page = page_for(template_key, data)
    if not page or not config.SITE_URL:
        return ""
    return config.SITE_URL + page


def render(template_key: str, extra: str = "", **kw) -> tuple[str, str]:
    """按模板库渲染标题与正文；缺占位符时保留原样，不让通知发不出去。"""
    title_tpl, body_tpl = TEMPLATES.get(template_key, ("平台通知", "{extra}"))
    kw = {**kw, "extra": extra}

    missing: set[str] = set()

    class _Safe(dict):
        def __missing__(self, k):
            # 缺参时保留占位并记一笔：静默留一串 {shift} 发给志愿者，
            # 没人会发现——真发生过。
            missing.add(k)
            return "{" + k + "}"

    title = title_tpl.format_map(_Safe(kw))
    body = body_tpl.format_map(_Safe(kw))
    if missing:
        log.error("消息模板 %s 缺少占位符 %s，发出去的正文里会留下花括号",
                  template_key, "、".join(sorted(missing)))
    return title, body


def real_mode() -> bool:
    """是否走真实推送。开关打开时 appid、secret、网关已在启动时核对齐全。"""
    return config.MSGCENTER_ENABLED


def send_user(db: OrmSession, user: User, template_key: str,
              data: dict | None = None, *, need_ack: bool = False,
              batch_id: str | None = None, **fields) -> None:
    """向单个用户发模板消息，并把送达记录写进调用方的事务。

    消息与触发它的业务变更同生共死：业务回滚了，通知也不该留下。
    真实推送失败只标记 delivered=False，不打断业务。
    need_ack=True 的消息要志愿者点一次「我已知悉」才算到人，用在管理者要逐人
    看签收情况的群发上（活动管理页「签收情况」）。审批待办不要签收：办没办，
    审批页上的状态就说明了，再要一次签收只是多一条要点的待办。
    """
    title, content = render(template_key, **fields)
    real = real_mode()
    url = _link_for(template_key, data, need_ack)
    row = Notification(id=uid("ntf"), user_id=user.id,
                       template_key=template_key, title=title,
                       content=content, data=data or {},
                       # 真实推送先记未送达，推成功了再改；进程在中间死掉的话，
                       # 库里说的「没送到」至少不是假话
                       delivered=not real,
                       need_ack=need_ack, batch_id=batch_id)
    db.add(row)
    if real:
        # 真正的推送等**这个事务提交之后**、而且**场次锁的 with 块结束之后**再做：
        # 业务回滚了就不该发出去；锁里做外呼会占着进程名额干等（见 locks.after_unlock）
        db.info.setdefault(_OUTBOX, []).append(
            (row.id, user.student_id, title, content, url))


_OUTBOX = "msgcenter_outbox"


@event.listens_for(SessionLocal, "after_commit")
def _push_after_commit(session) -> None:
    items = session.info.pop(_OUTBOX, None)
    if items:
        locks.after_unlock(lambda: _push(items))


@event.listens_for(SessionLocal, "after_rollback")
def _drop_after_rollback(session) -> None:
    session.info.pop(_OUTBOX, None)


def _push(items: list[tuple[str, str, str, str, str]]) -> None:
    """把已提交的通知真正推出去，推成功的回写 delivered。

    推送失败只留着 delivered=False，不打断业务。
    """
    ok: list[str] = []
    for ntf_id, student_id, title, content, url in items:
        try:
            if _send(student_id, title, content, url):
                ok.append(ntf_id)
        except Exception:  # noqa: BLE001  外部接口不可用不能拖垮业务
            log.warning("消息中心推送失败：%s → %s", student_id, title,
                        exc_info=True)
    if not ok:
        return
    db = SessionLocal()
    try:
        db.execute(update(Notification).where(Notification.id.in_(ok))
                   .values(delivered=True))
        db.commit()
    finally:
        db.close()


def _send(student_id: str, title: str, content: str, url: str = "") -> bool:
    """经开放平台消息中心下发一条消息，返回是否真的送达。

    收件人用 `uc_ux` + `(学号)`：由消息中心按学号去查这个人的企业微信地址。
    我们不需要、也不该知道他的手机号。
    """
    payload = {
        "title": title,
        "content": content,
        # receivers 用请求示例里的**真数组**。参数表把它标成 string 是文档笔误：
        # 换成 json.dumps 的字符串，网关会回 code=11「参数格式有误」。
        "receivers": [{"type": "uc_ux", "address": f"({student_id})"}],
        "channels": config.MSGCENTER_CHANNELS,
        "plannedTime": "",     # 空串 = 立即发送（文档示例即如此）
        # extras 是消息中心的「扩展属性」。im_linkUrl = 消息外链，卡片点击后
        # 打开它；写进正文是行不通的——链接词在卡片里看不出来可点，正文里
        # 放多个 <a> 也只认第一个。没有链接的模板给空对象，跟请求示例一致。
        "extras": {"im_linkUrl": url} if url else {},
    }
    return _delivered(sudy_sign.post_json(SEND_PATH, payload), student_id, title)


def _delivered(data: dict, student_id: str, title: str) -> bool:
    """从返回里判断「这个人到底收到没有」。

    `successed=true` 只说明请求被受理了：收件人写错时它在 wrongAddress 里，
    某个通道失败时在 channelErrorInfos 里，这两种都不算送达。我们每次只发
    一个人，所以这两个列表非空就等于没送到——不能因为 successed=true
    就记 delivered，那会让「推送失败」在库里看起来是成功的。
    """
    wrong = data.get("wrongAddress") or []
    failed = data.get("channelErrorInfos") or []
    if wrong or failed:
        log.warning("消息未送达 %s（%s）：wrongAddress=%s channelErrorInfos=%s",
                    student_id, title, wrong, failed)
        return False
    return True
