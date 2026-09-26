"""消息中心适配：模板消息推送。

模板库对应《设计方案》四.3「消息模板库」五个场景：
  报名成功 / 候补转正 / 岗位分配 / 活动变更警告

Mock 模式：把消息落库（notifications 表），前端「消息中心」即收件箱。
真实模式：经学校开放平台的消息中心（opengw）下发。

**本平台只发企业微信通道（channels=4）**。学校未同意本平台发短信，而短信是按条
计费的真金白银——所以 `_channels()` 里对通道 1 做了硬拦截，管理员在「集成设置」
页填了也不发。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import event, update
from sqlalchemy.orm import Session as OrmSession

from .. import config, locks, sudy_sign
from ..database import SessionLocal
from ..models import Notification, User, uid
from . import get_config, is_mock

log = logging.getLogger(__name__)

# 开放网关下的接口路径（域名为 config.OPENPLATFORM_GATEWAY）
SEND_PATH = "com.sudytech.msgcenter/ucp/_web/_apps/ucpmsg/api/v3/sendMessage.rst"

# 模板库：key → (标题模板, 正文模板)。{xx} 为占位符。
TEMPLATES: dict[str, tuple[str, str]] = {
    "signup_success": (
        "报名成功｜{activity}",
        "您已成功报名「{activity}」· {shift}。\n时间：{time}\n{detail}"
        "\n\n如无法参加，请于活动开始前24小时退选，临期退选将被记录。"),
    "waitlist_queued": (
        "已加入候补｜{activity}",
        "「{activity}」· {shift} 名额已满，您已报名候补，当前队列第 {pos} 位。"
        "\n有名额时将通知您确认。"),
    "hours_approved": (
        "志愿时长已核准｜{activity}",
        "「{activity}」的志愿时长已核准：您本次记 {hours} 小时。"
        "\n请于 {deadline} 前到「志愿时长」页确认或提出申诉，超时未确认则视为无误，"
        "之后修改需报审批。{pay_line}"),
    "hours_locked": (
        "志愿时长已锁定｜{activity}",
        "「{activity}」的志愿时长已锁定，{result}，活动已完成。"
        "\n感谢您的参与！"),
    "application_accepted": (
        "录取通知｜{activity}",
        "恭喜！您已被「{activity}」录取，安排在 {shift}（{time}）。"
        "\n具体岗位以负责人后续分配为准。请到「我的报名」查看志愿者联络群二维码，"
        "入群后请把群昵称改为「姓名+电话」。"),
    # 措辞留余地：这一次没排上不代表什么，也不留任何记录
    "application_rejected": (
        "报名结果｜{activity}",
        "感谢您报名「{activity}」。本次名额有限，暂未能安排您参加，"
        "期待下次活动再见。"),
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
        "您在「{activity}」已被老师选为岗位「{position}」组长。\n"
        "时间：{position_time}\n地点：{position_loc}\n"
        "请提前与负责人对接现场安排。"),
    "checkin_reminder": (
        "签到提醒｜{activity}",
        "您报名的「{activity}」· {shift} 即将开始。\n"
        "岗位：{position}\n地点：{loc}\n"
        "请按要求到岗并扫码签到；服务结束后记得签退。"),
    # 催签收：本身不要签收，否则催一次就多一条待办，5 个人的批次会变成 10 条
    "ack_reminder": (
        "待签收提醒｜{title}",
        "您有一条通知未签收：\n「{title}」\n"
        "请到消息中心点击「我已知悉」，方便统计签收情况。"),
    "custom": (
        "{title}",
        "{content}"),
}


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


def real_mode(db: OrmSession | None = None) -> bool:
    """是否走真实推送。

    比 `is_mock()` 多要一个 secret：开关打开但密钥没配时，请求会带着一份空签名
    打到学校网关上，换回一句看不懂的「签名校验失败」——不如老实在本地落库，
    前端「消息中心」照样能看到内容。
    """
    # 先看密钥：没配就不必去库里读开关（演示期每次发通知都省一次查询）
    return bool(config.OPENPLATFORM_SECRET) and not is_mock("msgcenter", db)


def send_user(db: OrmSession, user: User, template_key: str,
              data: dict | None = None, *, need_ack: bool = False,
              batch_id: str | None = None, **fields) -> None:
    """向单个用户发模板消息，并把送达记录写进调用方的事务。

    消息与触发它的业务变更同生共死：业务回滚了，通知也不该留下。
    真实推送失败只标记 delivered=False，不打断业务。
    need_ack=True 的消息要志愿者点一次「我已知悉」才算到人。
    """
    title, content = render(template_key, **fields)
    real = real_mode(db)    # 用调用方的会话读配置，别另占一条连接
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
            (row.id, user.student_id, title, content))


_OUTBOX = "msgcenter_outbox"


@event.listens_for(SessionLocal, "after_commit")
def _push_after_commit(session) -> None:
    items = session.info.pop(_OUTBOX, None)
    if items:
        locks.after_unlock(lambda: _push(items))


@event.listens_for(SessionLocal, "after_rollback")
def _drop_after_rollback(session) -> None:
    session.info.pop(_OUTBOX, None)


def _push(items: list[tuple[str, str, str, str]]) -> None:
    """把已提交的通知真正推出去，推成功的回写 delivered。

    推送失败只留着 delivered=False，不打断业务。
    """
    ok: list[str] = []
    for ntf_id, student_id, title, content in items:
        try:
            if _send(student_id, title, content):
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


def _channels(cfg: dict) -> str:
    """通道清单，**永远不含 1（短信）**。

    管理员在「集成设置」里填了 1 也只是记一条日志、不发：本平台没有发短信的
    许可，而短信一发就是真实计费、真实打扰。宁可少发一条，也不能悄悄发出去。
    """
    keep: list[str] = []
    for code in str(cfg.get("channels") or "4").split(","):
        code = code.strip()
        if not code:
            continue
        if code == "1":
            log.warning("集成设置里配了短信通道(1)，已忽略：本平台不使用短信")
            continue
        keep.append(code)
    return ",".join(keep) or "4"


def _send(student_id: str, title: str, content: str) -> bool:
    """经开放平台消息中心下发一条消息，返回是否真的送达。

    收件人用 `uc_ux` + `(学号)`：由消息中心按学号去查这个人的企业微信地址。
    我们不需要、也不该知道他的手机号。
    """
    cfg = get_config("msgcenter")
    payload = {
        "title": title,
        "content": content,
        # 文档参数表把 receivers 标成 string，但请求示例给的是 json 数组——
        # 以示例为准。真机第一次调用时留意这里，若报参数格式错就改回字符串。
        "receivers": json.dumps([{"type": "uc_ux", "address": f"({student_id})"}]),
        "channels": _channels(cfg),
        "plannedTime": "",     # 空串 = 立即发送（文档示例即如此）
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
