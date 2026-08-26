"""审计留痕服务。

《设计方案》四.2「谁动了我的岗位」：
  所有老师/青协的增删改 → audit_logs；
  活动相关且 visible=True 的记录在活动详情页对志愿者展示（倒序前10条）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from .models import AuditLog, uid


def log(db: OrmSession, *, actor, action: str, target_type: str,
        target_id: str = "", target_label: str = "", activity_id: str | None = None,
        summary: str = "", changes: list[dict] | None = None,
        visible: bool = True) -> AuditLog:
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
    "export": "导出", "hours_confirm": "确认时长", "hours_upload": "回传时长",
    "credit_adjust": "信用分调整", "restriction": "限制准入设置",
    "violation": "违规登记",
    "checkin_task": "签到任务", "manual_checkin": "手动补签",
    "integration": "集成配置",
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
