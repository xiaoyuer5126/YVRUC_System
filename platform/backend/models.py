"""数据模型。

核心是设计方案第二节的**一二级岗位树**：
  Activity（活动）── PositionGroup（一级岗位/场次，时间冲突检测的单位）
                        └─ Station（二级工位，实际排班/签到的单位）

报名 Enrollment 挂在「一级岗位」上（锁定时段），station_id 在两种情况下才有值：
  · 模式A（精细化）：学生直接选工位，报名即带 station_id；
  · 调配期：待分配组经排班程序/人工二次分配后回填。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import config
from .database import Base


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> datetime:
    return datetime.now()


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="minutes") if dt else None


# ======================= 用户与会话 =======================

# 用户身份（可多个）：管理员 / 老师 / 青协 / 学生
ROLES = ("admin", "teacher", "qingxie", "student")
ROLE_LABELS = {"admin": "管理员", "teacher": "老师", "qingxie": "青协", "student": "学生"}
# 可被设为活动管理者的身份（老师 / 青协；管理员天然全权）
MANAGER_ROLES = ("teacher", "qingxie")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    gender: Mapped[str] = mapped_column(String(8), default="")
    college: Mapped[str] = mapped_column(String(64), default="")
    grade: Mapped[str] = mapped_column(String(16), default="")       # 如 2023
    phone: Mapped[str] = mapped_column(String(32), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    # 数智人大拿不到微信号/身份证号，需志愿者自行完善后才能报名
    wechat: Mapped[str] = mapped_column(String(64), default="")
    # 身份证号由数智人大提供，仅存储不外显（导出表也不含，见 exporter.py）
    id_card: Mapped[str] = mapped_column(String(64), default="")
    # —— 类型与身份 ——
    # 类型二选一（老师/学生）；身份可多个（管理员/老师/青协/学生），可切换视角。
    user_type: Mapped[str] = mapped_column(String(12), default="student")  # teacher / student
    roles: Mapped[list] = mapped_column(JSON, default=list)                # ["student", ...]

    # —— 信用体系 ——
    credit_score: Mapped[int] = mapped_column(Integer, default=config.CREDIT_FULL)
    credit_frozen_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    abandon_count: Mapped[int] = mapped_column(Integer, default=0)   # 连续放弃候补次数
    waitlist_banned_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # —— 限制准入（《志愿者服务规范》）：不得参与的活动类型 + 截止时间 ——
    restricted_types: Mapped[list] = mapped_column(JSON, default=list)   # ["C","D"]
    restricted_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def has_role(self, *roles: str) -> bool:
        return bool(set(roles) & set(self.roles or []))

    def is_admin(self) -> bool:
        return self.has_role("admin")

    def is_manager_identity(self) -> bool:
        """能否进入工作台/被设为活动管理者：老师 / 青协（管理员天然可以）。"""
        return self.is_admin() or self.has_role(*MANAGER_ROLES)

    def profile_complete(self) -> bool:
        """资料完善度：手机号/微信号/身份证号齐全（老师类型不要求）。"""
        if self.user_type == "teacher":
            return True
        return bool((self.phone or "").strip()
                    and (self.wechat or "").strip()
                    and (self.id_card or "").strip())

    def active_role_label(self) -> str:
        if not self.roles:
            return "学生"
        return "、".join(ROLE_LABELS.get(r, r) for r in self.roles)

    def brief(self) -> dict:
        return {
            "id": self.id, "name": self.name, "student_id": self.student_id,
            "gender": self.gender, "college": self.college, "grade": self.grade,
            "user_type": self.user_type, "roles": self.roles or [],
            "role_label": self.active_role_label(),
        }

    def full(self) -> dict:
        """含联系方式。仅在本人/有权限的后台接口下发。"""
        frozen = self.credit_frozen_until and self.credit_frozen_until > now()
        banned = self.waitlist_banned_until and self.waitlist_banned_until > now()
        return {
            **self.brief(),
            "phone": self.phone, "email": self.email, "wechat": self.wechat,
            "profile_complete": self.profile_complete(),
            "credit_score": self.credit_score,
            "credit_frozen": bool(frozen),
            "credit_frozen_until": iso(self.credit_frozen_until),
            "abandon_count": self.abandon_count,
            "waitlist_banned": bool(banned),
            "restricted_types": self.restricted_types or [],
            "restricted_until": iso(self.restricted_until),
        }


class Session(Base):
    """登录会话。expires_at 到点即失效，由 deps.current_user 校验并清理。"""
    __tablename__ = "sessions"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


# ======================= 活动与岗位树 =======================

class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(2), default="A")         # A/B/C/D
    intro: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(128), default="")
    contact_info: Mapped[str] = mapped_column(String(256), default="")   # 公示联系方式
    # 结构化联系人（多人）：[{"name": 姓名, "phone": 电话}]；导出/展示优先用它
    contacts: Mapped[list] = mapped_column(JSON, default=list)
    qa_group_qr: Mapped[str] = mapped_column(Text, default="")           # 答疑群二维码(dataURL)，平台只做展示容器
    cover_hue: Mapped[int] = mapped_column(Integer, default=0)           # 封面色相（演示用渐变色卡）

    status: Mapped[str] = mapped_column(String(16), default="draft")
    # draft 筹备中 / published 报名中 / closed 已截止 / finished 已结束

    reg_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reg_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    groups: Mapped[list["PositionGroup"]] = relationship(
        back_populates="activity", order_by="PositionGroup.start_time",
        cascade="all, delete-orphan")

    STATUS_LABELS = {"draft": "筹备中", "published": "报名中",
                     "closed": "已截止", "finished": "已结束"}

    def label_status(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)

    def time_span(self) -> tuple[datetime, datetime] | None:
        """活动整体起止 = 一级岗位的最早开始 / 最晚结束。"""
        if not self.groups:
            return None
        return min(g.start_time for g in self.groups), max(g.end_time for g in self.groups)


class ActivityManager(Base):
    """活动管理者（多人共管）。

    · 创建者自动成为管理者，且**不可被移除**（created_by 只是记录信息，
      权限上不特殊）；
    · 管理者之间权限完全相同，任何管理者可添加/移除其他管理者；
    · 被添加者必须持有 老师/青协 身份（管理员无需添加，天然可见全部）。
    """
    __tablename__ = "activity_managers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    added_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PositionGroup(Base):
    """一级岗位（场次）：总时间段 + 总人数上限 + 岗位模式。

    时间冲突检测以这一层为准——学生报了这组，该时段即被锁定。
    **时间完全由一级岗位负责**；二级工位只区分职责/地点。

    mode 三种（创建/编辑时选定）：
      single  仅一级岗位：无二级工位，报名即占该场次名额
      assign  二级待定：报名只锁时段，截止后由排班程序统一分工位
      fine    直接定二级：报名时直接选定二级工位（工位间默认互斥）
    """
    __tablename__ = "position_groups"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    capacity: Mapped[int] = mapped_column(Integer)                 # 总人数上限，至少 1
    waitlist_quota: Mapped[int] = mapped_column(Integer, default=3)  # 候补队列上限
    mode: Mapped[str] = mapped_column(String(12), default="fine")  # single / assign / fine
    note: Mapped[str] = mapped_column(String(512), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)

    activity: Mapped[Activity] = relationship(back_populates="groups")
    stations: Mapped[list["Station"]] = relationship(
        back_populates="group", order_by="Station.sort",
        cascade="all, delete-orphan")

    def span_text(self) -> str:
        same_day = self.start_time.date() == self.end_time.date()
        if same_day:
            return (f"{self.start_time:%m-%d} {self.start_time:%H:%M}"
                    f"-{self.end_time:%H:%M}")
        return f"{self.start_time:%m-%d %H:%M} ~ {self.end_time:%m-%d %H:%M}"

    def station_quota_sum(self) -> int:
        return sum(s.quota for s in self.stations)

    MODE_LABELS = {"single": "仅一级岗位", "assign": "二级待定", "fine": "直接定二级"}

    def mode_label(self) -> str:
        return self.MODE_LABELS.get(self.mode, self.mode)

    def needs_assign(self) -> bool:
        """二级待定：报名只锁时段，工位由调配期统一分配。"""
        return self.mode == "assign"


class Station(Base):
    """二级工位：同一场次下的职责/地点划分。

    工位没有自己的时间——时间统一由一级岗位负责，同场次内的工位互斥
    （一人只占一个），因此把人分进工位不会产生新的时段冲突。"""
    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("position_groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    location: Mapped[str] = mapped_column(String(128), default="")
    quota: Mapped[int] = mapped_column(Integer, default=1)
    # 结构化必选条件 {标签: 值或值列表}，转成排班程序的条件树；空 = 不限
    requirement_tags: Mapped[dict] = mapped_column(JSON, default=dict)
    requirement_note: Mapped[str] = mapped_column(String(512), default="")  # 要求原文（人看）
    training_note: Mapped[str] = mapped_column(String(512), default="")     # 培训内容说明
    sort: Mapped[int] = mapped_column(Integer, default=0)

    group: Mapped[PositionGroup] = relationship(back_populates="stations")

    def time_text(self) -> str:
        """工位无独立时间：展示所属场次的时段。"""
        return self.group.span_text() if self.group else ""


# ======================= 报名 / 候补 / 分配 =======================

class Enrollment(Base):
    """一条报名 = （用户 × 一级岗位）。一人对同一组只有一条有效记录。

    status 流转：
      confirmed        正式报名（即选即得 / 候补转正确认 / 管理员添加）
      waitlisted       候补排队（按 joined_at 先后）
      pending_confirm  候补转正，等待用户在 confirm_deadline 前确认
      cancelled        已退选（cancel_type: normal 正常 / late 临近扣分 /
                                     expired 放弃候补超时 / abandoned 主动放弃候补 /
                                     group_deleted 场次被删除）

    seat_held：临近退选（开始前 24 小时内）扣分且**名额不再释放**，
    该记录虽已 cancelled 但仍占着一个名额，防止有人临期退了、别人补进来、
    再退一次，把现场人数搅乱。
    """
    __tablename__ = "enrollments"
    __table_args__ = (
        # 一人在一个场次只能有一条有效记录（含占位不释放的临近退选记录）。
        # 这是并发抢报的兜底：Redis 锁没拿住也插不进第二条。
        Index("uq_enrollment_live", "group_id", "user_id", unique=True,
              postgresql_where=text(
                  "status IN ('confirmed', 'waitlisted', 'pending_confirm')"
                  " OR seat_held")),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("position_groups.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)

    status: Mapped[str] = mapped_column(String(24), default="confirmed", index=True)
    cancel_type: Mapped[str] = mapped_column(String(16), default="")
    seat_held: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # 志愿者报名时表态：是否接受管理者把自己调到别的工位。
    # **只是参考**——管理者依然可以调，界面上会提示「本人不接受调剂」。
    allow_reassign: Mapped[bool] = mapped_column(Boolean, default=True)

    station_id: Mapped[str | None] = mapped_column(
        ForeignKey("stations.id"), nullable=True)          # 二级工位（见类注释）
    assign_forced: Mapped[bool] = mapped_column(Boolean, default=False)  # 违规强排标记
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    assigned_by: Mapped[str | None] = mapped_column(String(32), nullable=True)

    source: Mapped[str] = mapped_column(String(16), default="self")  # self/promoted/admin
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=now)  # 排队/报名时间
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirm_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def active(self) -> bool:
        return self.status in ("confirmed", "waitlisted", "pending_confirm")


# ======================= 签到 =======================

class CheckinTask(Base):
    """签到任务：统一签到（挂一级岗位）或分时分工位签到（挂具体工位）。

    token 是签到码的核心串；对接学校现有签到系统时由 checkin_gateway
    适配器换成对方返回的任务号，本表结构不变。
    """
    __tablename__ = "checkin_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("position_groups.id"), index=True)
    station_id: Mapped[str | None] = mapped_column(ForeignKey("stations.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(12), default="group")   # group / station
    token: Mapped[str] = mapped_column(String(16), unique=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class CheckinRecord(Base):
    __tablename__ = "checkin_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("checkin_tasks.id"), index=True)
    activity_id: Mapped[str] = mapped_column(String(32), index=True)
    group_id: Mapped[str] = mapped_column(String(32), index=True)
    station_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    method: Mapped[str] = mapped_column(String(12), default="scan")  # scan / manual
    by_user: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 手动补签操作人
    checkin_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    # —— 签退 ——
    checkout_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checkout_method: Mapped[str] = mapped_column(String(12), default="")
    # scan 扫码签退 / manual 手动签退 / auto 超时自动按场次结束时间兜底
    checkout_by: Mapped[str | None] = mapped_column(String(32), nullable=True)


# ======================= 信用分 / 时长 / 审计 / 通知 / 集成配置 =======================

class CreditLog(Base):
    __tablename__ = "credit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    after_score: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(256))
    activity_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    operator_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 空=系统规则
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class HoursRecord(Base):
    __tablename__ = "hours_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    hours: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")  # confirmed/uploaded
    source: Mapped[str] = mapped_column(String(12), default="checkin")    # checkin 签到统计 / manual 手动添加
    note: Mapped[str] = mapped_column(String(256), default="")            # 手动添加时的备注
    upload_ref: Mapped[str] = mapped_column(String(64), default="")       # 数智人大回执号
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AuditLog(Base):
    """操作留痕中心。老师/青协的增删改全记这里，志愿者可在活动页看到摘要。

    changes 结构: [{"field": 字段名, "label": 中文列名, "old": 旧值, "new": 新值}]
    """
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(64), default="系统")
    action: Mapped[str] = mapped_column(String(32))       # create/update/delete/publish/...
    target_type: Mapped[str] = mapped_column(String(24))  # activity/group/station/enrollment/assignment/user/export/hours/integration
    target_id: Mapped[str] = mapped_column(String(48), default="")
    target_label: Mapped[str] = mapped_column(String(160), default="")
    activity_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(String(512), default="")
    changes: Mapped[list] = mapped_column(JSON, default=list)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)  # 是否对志愿者可见
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def out(self) -> dict:
        return {
            "id": self.id, "actor_name": self.actor_name, "action": self.action,
            "target_type": self.target_type, "target_label": self.target_label,
            "summary": self.summary, "changes": self.changes or [],
            "created_at": iso(self.created_at), "visible": self.visible,
        }


class Notification(Base):
    """消息通知。演示模式下代表「企业微信模板消息」的送达记录。"""
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    template_key: Mapped[str] = mapped_column(String(32), default="")   # 见 integrations/wecom.py 模板库
    channel: Mapped[str] = mapped_column(String(16), default="wecom")
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    delivered: Mapped[bool] = mapped_column(Boolean, default=True)      # wecom mock 恒 True
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def out(self) -> dict:
        return {
            "id": self.id, "title": self.title, "content": self.content,
            "template_key": self.template_key, "read": self.read,
            "data": self.data or {}, "created_at": iso(self.created_at),
        }


class IntegrationConfig(Base):
    """外部系统对接参数。key → JSON。

    固定四个 key：
      shuzhi_renda / wecom_work / checkin_gateway / sso
    enabled=false 时走内置 Mock 实现（演示模式），true 时走真实 HTTP 实现。
    """
    __tablename__ = "integration_configs"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
