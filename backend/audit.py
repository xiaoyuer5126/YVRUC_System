"""审计留痕服务。

「谁动了我的岗位」：
  所有发起方/青协的增删改 → audit_logs；
  活动相关且 visible=True 的记录在活动详情页对志愿者展示（倒序前10条）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from .models import AFFECTED_FIELD, IDENTITY_FIELD, Activity, AuditLog, uid


def log(db: OrmSession, *, actor, action: str, target_type: str,
        target_id: str = "", target_label: str = "", activity_id: str | None = None,
        summary: str = "", changes: list[dict] | None = None,
        visible: bool = False, affected: list[str] | None = None) -> AuditLog:
    """记一条审计。

    visible 默认 **False**：留痕里什么都有——签到码、他人学号、信用分处分——
    默认公开等于「忘了标就泄露」：签到码整串进了志愿者可见的变更记录，
    学生不到现场也能签到。
    需要对志愿者公开的，显式传 visible=True，且只限「谁动了我的岗位」
    这类结构性变更：活动信息、场次、岗位的增删改。

    affected：这次改动影响到、但按留痕的 target 找不到的人：删场次时被取消报名的
    人（库里已经没有他名下的报名），或者改的是全组共用一份的字段、而组长站在同组
    另一个岗位上（见 staff_activities.update_position 的组长任务）。编辑态结束时
    按人汇总的变更通知（notify.notify_edit_changes）靠它找到这些人。
    """
    # 活动公开之前的改动一律不进志愿者的「变更记录」。筹备期的每一次试错
    # ——建了又删的场次、改来改去的名额——学生从来没见过那些东西，摆出来
    # 只会让人以为安排一直在变，还把内部过程暴露了出去。
    # 「变更记录」的意思是「你报名之后发生了什么变化」，得从公开那一刻算起。
    if visible and activity_id:
        act = db.get(Activity, activity_id)
        if act is not None and act.status in ("draft", "pending", "approved"):
            visible = False
    # 以哪个身份操作的（前端的 X-Identity）、影响到哪些人。没有专门的列——
    # 线上没有迁移，加列要手工 ALTER——就挂在 changes 末尾带下划线字段名的
    # 记录上，AuditLog.out() 把它们摘掉，不会混进变更明细
    changes = list(changes or [])
    if affected:
        changes.append({"field": AFFECTED_FIELD, "label": "受影响的人",
                        "old": "", "new": ",".join(sorted(set(affected)))})
    view = getattr(actor, "view", lambda: None)()
    if view:
        changes.append({"field": IDENTITY_FIELD, "label": "操作身份",
                        "old": "", "new": view})
    entry = AuditLog(
        id=uid("aud"),
        actor_id=getattr(actor, "id", None),
        actor_name=getattr(actor, "name", "系统") or "系统",
        action=action, target_type=target_type, target_id=target_id,
        target_label=target_label[:150], activity_id=activity_id,
        summary=summary[:500], changes=changes, visible=visible,
    )
    db.add(entry)
    # 不在这里 commit：与业务写入同一事务，保证留痕和变更原子生效。
    return entry


ACTION_LABELS = {
    "create": "创建", "update": "修改", "delete": "删除",
    "publish": "发布", "close": "截止报名", "finish": "结束活动",
    "assign": "岗位分配", "assign_manual": "手动调岗", "force_assign": "强制分配",
    "export": "导出", "hours_confirm": "时长核准", "roster_import": "名单导入",
    "violation": "违规登记",
    "checkin_task": "签到任务", "manual_checkin": "手动补签",
    "integration": "集成配置", "copy": "复制",
    # 志愿者自己的动作（人员变动流水）
    "enroll": "报名", "waitlist": "加入候补", "cancel": "退选",
    "quit_queue": "退出候补", "abandon": "放弃候补", "promote": "候补转正",
    "excuse": "请假", "unexcuse": "销假",
    "material": "学习材料",
    "submit": "提交审批", "approve": "审批", "view_team": "查看组员",
    "duty": "值班交接", "vb_confirm": "志愿北京确认",
    "feedback": "意见反馈",
}


def diff_changes(before: dict, after: dict, labels: dict[str, str] | None = None) -> list[dict]:
    """对比新旧字段值，生成 changes 列表。值按人话写（见 value_text）：
    存进去的是字符串，改完再想还原成「王（139…）」就晚了。"""
    out = []
    for key, old in (before or {}).items():
        new = (after or {}).get(key)
        if old != new:
            out.append({"field": key, "label": (labels or {}).get(key, key),
                        "old": value_text(old), "new": value_text(new)})
    return out


def value_text(v) -> str:
    """留痕里的字段值拼成人话。

    联系人、招募范围这些字段存的是列表/字典，str() 出来是
    [{'name': '王', 'phone': '139…'}] 或 ['苏州校区']，念给人听等于没写。
    空值返回空串，由各处自己决定写「（空）」还是「空」。
    """
    if v is None or v == "" or v == [] or v == {}:
        return ""
    if isinstance(v, dict):
        return _one_value(v)
    if isinstance(v, (list, tuple)):
        return "、".join(_one_value(x) for x in v)
    return str(v)


def _one_value(x) -> str:
    if isinstance(x, dict):
        if x.get("name"):        # 联系人：写「姓名（电话）」，键名不必念出来
            phone = x.get("phone")
            return f"{x['name']}（{phone}）" if phone else str(x["name"])
        return "、".join(f"{k}：{value_text(val) or '（空）'}" for k, val in x.items())
    return str(x)
