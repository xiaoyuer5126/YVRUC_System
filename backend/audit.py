"""审计留痕服务。

《设计方案》四.2「谁动了我的岗位」：
  所有发起方/青协的增删改 → audit_logs；
  活动相关且 visible=True 的记录在活动详情页对志愿者展示（倒序前10条）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from .models import Activity, AuditLog, uid


def log(db: OrmSession, *, actor, action: str, target_type: str,
        target_id: str = "", target_label: str = "", activity_id: str | None = None,
        summary: str = "", changes: list[dict] | None = None,
        visible: bool = False) -> AuditLog:
    """记一条审计。

    visible 默认 **False**：留痕里什么都有——签到码、他人学号、信用分处分——
    默认公开等于「忘了标就泄露」。曾经真的把签到码整串写进了志愿者可见的
    变更记录，学生不到现场也能签到。
    需要对志愿者公开的，显式传 visible=True，且只限「谁动了我的岗位」
    这类结构性变更：活动信息、场次、岗位的增删改。
    """
    # 活动公开之前的改动一律不进志愿者的「变更记录」。筹备期的每一次试错
    # ——建了又删的场次、改来改去的名额——学生从来没见过那些东西，摆出来
    # 只会让人以为安排一直在变，还把内部过程暴露了出去。
    # 「变更记录」的意思是「你报名之后发生了什么变化」，得从公开那一刻算起。
    if visible and activity_id:
        act = db.get(Activity, activity_id)
        if act is not None and act.status in ("draft", "pending", "approved"):
            visible = False
    entry = AuditLog(
        id=uid("aud"),
        actor_id=getattr(actor, "id", None),
        actor_name=getattr(actor, "name", "系统") or "系统",
        action=action, target_type=target_type, target_id=target_id,
        target_label=target_label[:150], activity_id=activity_id,
        summary=summary[:500], changes=changes or [], visible=visible,
    )
    db.add(entry)
    # 不在这里 commit：与业务写入同一事务，保证留痕和变更原子生效。
    return entry


ACTION_LABELS = {
    "create": "创建", "update": "修改", "delete": "删除",
    "publish": "发布", "close": "截止报名", "finish": "结束活动",
    "assign": "岗位分配", "assign_manual": "手动调岗", "force_assign": "强制分配",
    "export": "导出", "hours_confirm": "时长核准",
    "violation": "违规登记",
    "checkin_task": "签到任务", "manual_checkin": "手动补签",
    "integration": "集成配置", "copy": "复制",
    # 志愿者自己的动作（人员变动流水）
    "enroll": "报名", "waitlist": "加入候补", "cancel": "退选",
    "quit_queue": "退出候补", "abandon": "放弃候补", "promote": "候补转正",
    "material": "学习材料",
}


def diff_changes(before: dict, after: dict, labels: dict[str, str] | None = None) -> list[dict]:
    """对比新旧字段值，生成 changes 列表。"""
    out = []
    for key, old in (before or {}).items():
        new = (after or {}).get(key)
        if old != new:
            out.append({"field": key, "label": (labels or {}).get(key, key),
                        "old": "" if old is None else str(old),
                        "new": "" if new is None else str(new)})
    return out
