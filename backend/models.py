"""数据模型。

结构是两层：
  Activity（活动）── Shift（场次：只管时间，时段冲突检测的单位）
                        └─ Position（岗位：具体职责、名额、条件、组长）

报名 Enrollment 挂在场次上（锁定时段）。position_id 在两种情况下有值：
  · pick 模式：报名即选岗位，落库就带上；
  · assign 模式：报名只锁时段，截止后由一键分配回填。
一人在同一场次只能占一个岗位（唯一约束兜底）。
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
    # 身份证号仅存储不外显（导出表也不含，见 exporter.py）
    id_card: Mapped[str] = mapped_column(String(64), default="")
    # 港澳台学生与留学生没有内地居民身份证号，勾选后免填
    no_mainland_id: Mapped[bool] = mapped_column(Boolean, default=False)
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
        """资料完善度：手机号 + 微信号 + 身份证号（老师类型不要求）。

        港澳台学生与留学生勾了「无内地身份证号」的，这一项直接跳过——
        他们本来就开不出这个号，卡着不让报名没有道理。
        """
        if self.user_type == "teacher":
            return True
        if not ((self.phone or "").strip() and (self.wechat or "").strip()):
            return False
        return self.no_mainland_id or bool((self.id_card or "").strip())

    def masked_id_card(self) -> str:
        card = (self.id_card or "").strip()
        if len(card) < 8:
            return ""
        return f"{card[:4]}{'*' * (len(card) - 7)}{card[-3:]}"

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
            "no_mainland_id": self.no_mainland_id,
            # 只回脱敏值：让本人看得出「已经填过了」，又不把号原样发出去
            "id_card_masked": self.masked_id_card(),
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

    # —— 编辑冻结 ——
    # 管理者调整场次/岗位期间暂停报名：学生不该报进一个正在被改的岗位表，
    # 也不该看到改了一半的安排。点「保存并发布」解冻，超时自动解冻兜底。
    editing_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    editing_by: Mapped[str | None] = mapped_column(String(32), nullable=True)

    shifts: Mapped[list["Shift"]] = relationship(
        back_populates="activity", order_by="Shift.start_time",
        cascade="all, delete-orphan")

    STATUS_LABELS = {"draft": "筹备中", "published": "报名中",
                     "closed": "已截止", "finished": "已结束"}

    def label_status(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)

    def time_span(self) -> tuple[datetime, datetime] | None:
        """活动整体起止 = 最早场次开始 / 最晚场次结束。"""
        if not self.shifts:
            return None
        return (min(s.start_time for s in self.shifts),
                max(s.end_time for s in self.shifts))


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


class Shift(Base):
    """场次：一段服务时间。**时间只存在于这一层**，用于时段冲突检测。

    场次下挂具体岗位。两种模式：
      pick    报名即选岗位：名额与候补都在岗位上
      assign  截止后分配：报名只锁时段，名额与候补在场次上，
              截止后由一键分配把人分进岗位
    """
    __tablename__ = "shifts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    mode: Mapped[str] = mapped_column(String(12), default="pick")
    # 下面两个数字**只在 assign 模式下有意义**：报名时还没有岗位可选，
    # 名额只能记在场次上。pick 模式下名额与候补都在岗位上，这两个值不参与判断。
    capacity: Mapped[int] = mapped_column(Integer, default=0)
    waitlist_quota: Mapped[int] = mapped_column(Integer, default=3)
    note: Mapped[str] = mapped_column(String(512), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    # 签到提醒只发一次，发过就记时间（定时任务与手动按钮共用这个标记）
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    activity: Mapped[Activity] = relationship(back_populates="shifts")
    positions: Mapped[list["Position"]] = relationship(
        back_populates="shift", order_by="Position.sort",
        cascade="all, delete-orphan")

    MODE_LABELS = {"pick": "报名即选岗位", "assign": "截止后统一分配"}

    def span_text(self) -> str:
        same_day = self.start_time.date() == self.end_time.date()
        if same_day:
            return (f"{self.start_time:%m-%d} {self.start_time:%H:%M}"
                    f"-{self.end_time:%H:%M}")
        return f"{self.start_time:%m-%d %H:%M} ~ {self.end_time:%m-%d %H:%M}"

    def mode_label(self) -> str:
        return self.MODE_LABELS.get(self.mode, self.mode)

    def needs_assign(self) -> bool:
        return self.mode == "assign"

    def total_capacity(self) -> int:
        """场次总名额：assign 模式记在场次上，pick 模式是各岗位之和。"""
        if self.needs_assign():
            return self.capacity
        return sum(p.quota for p in self.positions)


class Position(Base):
    """岗位：场次下的具体职责。没有自己的时间——时间统一由场次负责，
    同场次内的岗位互斥（一人只占一个），所以换岗不会产生新的时段冲突。"""
    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    shift_id: Mapped[str] = mapped_column(ForeignKey("shifts.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    location: Mapped[str] = mapped_column(String(128), default="")
    quota: Mapped[int] = mapped_column(Integer, default=1)          # 至少 1
    waitlist_quota: Mapped[int] = mapped_column(Integer, default=3)
    # 硬性条件 {标签: 值或值列表}，空 = 不限。报名时直接拦截，
    # 一键分配时同样作为硬约束（见 assign.py）。
    requirement_tags: Mapped[dict] = mapped_column(JSON, default=dict)
    requirement_note: Mapped[str] = mapped_column(String(512), default="")
    training_note: Mapped[str] = mapped_column(String(512), default="")
    # 需要组长：报名时志愿者可表态愿意担任，先到先得，管理者可改判
    needs_leader: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)

    shift: Mapped[Shift] = relationship(back_populates="positions")

    def time_text(self) -> str:
        """岗位无独立时间：展示所属场次的时段。"""
        return self.shift.span_text() if self.shift else ""

    def requirement_desc(self) -> str:
        parts = []
        for tag, value in (self.requirement_tags or {}).items():
            vals = value if isinstance(value, list) else [value]
            vals = [str(v) for v in vals if str(v).strip()]
            if vals:
                parts.append(f"{tag}：{'/'.join(vals)}")
        return "；".join(parts)


class Attachment(Base):
    """学习材料：培训文档挂场次或岗位，志愿者在活动页下载。

    视频不自托管——大文件的上传成功率、带宽和备份代价都不划算，
    走 url 字段指向学校平台/网盘的链接。
    """
    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(12))      # shift / position
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    activity_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(8), default="file")   # file / link
    title: Mapped[str] = mapped_column(String(160))
    # kind=file：存盘文件名与大小；kind=link：外部地址
    stored_name: Mapped[str] = mapped_column(String(160), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    url: Mapped[str] = mapped_column(String(512), default="")
    uploaded_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ======================= 报名 / 候补 / 分配 =======================

class Enrollment(Base):
    """一条报名 = （用户 × 场次）。一人对同一场次只有一条有效记录，
    也就是同场次下只能占一个岗位——候补和正式都算。

    status 流转：
      confirmed        正式报名（即选即得 / 候补转正确认 / 管理员添加）
      waitlisted       候补排队（按 joined_at 先后）
      pending_confirm  候补转正，等待用户在 confirm_deadline 前确认
      cancelled        已退选（cancel_type: normal 正常 / late 临近扣分 /
                                     expired 放弃候补超时 / abandoned 主动放弃候补 /
                                     shift_deleted 场次被删除）

    seat_held：临近退选（开始前 24 小时内）扣分且**名额不再释放**，
    该记录虽已 cancelled 但仍占着一个名额，防止有人临期退了、别人补进来、
    再退一次，把现场人数搅乱。
    """
    __tablename__ = "enrollments"
    __table_args__ = (
        # 一人在一个场次只能有一条有效记录（含占位不释放的临近退选记录）。
        # 这是并发抢报的兜底：Redis 锁没拿住也插不进第二条。
        Index("uq_enrollment_live", "shift_id", "user_id", unique=True,
              postgresql_where=text(
                  "status IN ('confirmed', 'waitlisted', 'pending_confirm')"
                  " OR seat_held")),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    shift_id: Mapped[str] = mapped_column(ForeignKey("shifts.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)

    status: Mapped[str] = mapped_column(String(24), default="confirmed", index=True)
    cancel_type: Mapped[str] = mapped_column(String(16), default="")
    seat_held: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # 是否接受管理者把自己调到别的岗位。**只是参考**——管理者依然可以调，
    # 界面上会提示「本人不接受调剂」。候补必须接受调剂（报名时强制为真）。
    allow_reassign: Mapped[bool] = mapped_column(Boolean, default=True)

    # —— 组长 ——
    # 报名时表态愿意担任；先到先得，同岗位第一个有意愿的人自动成为组长。
    leader_willing: Mapped[bool] = mapped_column(Boolean, default=False)
    is_leader: Mapped[bool] = mapped_column(Boolean, default=False)

    position_id: Mapped[str | None] = mapped_column(
        ForeignKey("positions.id"), nullable=True, index=True)
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
    """签到任务：整场次一个统一码，或每个岗位一个独立码。

    token 是签到码的核心串；对接学校现有签到系统时由 checkin_gateway
    适配器换成对方返回的任务号，本表结构不变。
    """
    __tablename__ = "checkin_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    shift_id: Mapped[str] = mapped_column(ForeignKey("shifts.id"), index=True)
    position_id: Mapped[str | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(12), default="shift")   # shift / position
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
    shift_id: Mapped[str] = mapped_column(String(32), index=True)
    position_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
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


class ViolationRecord(Base):
    """违规记录（《志愿者服务规范》「限制准入名单」的一行）。

    规范要求每一条违规单独作一行、活动结束后由负责人填写。系统按累计次数
    给出限制准入建议，最终生效的类型与截止日由操作者确认后写入 User。
    """
    __tablename__ = "violation_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    activity_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    activity_title: Mapped[str] = mapped_column(String(128), default="")
    activity_type: Mapped[str] = mapped_column(String(2))     # A/B/C/D
    kind: Mapped[str] = mapped_column(String(16))             # violations.KINDS
    detail: Mapped[str] = mapped_column(String(512), default="")   # 违规情况简述
    has_proof: Mapped[bool] = mapped_column(Boolean, default=False)  # 请假是否有证明
    # 本次登记实际生效的限制（操作者确认后的值）
    applied_types: Mapped[list] = mapped_column(JSON, default=list)
    applied_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    basis: Mapped[str] = mapped_column(String(256), default="")    # 依据条文
    operator_id: Mapped[str] = mapped_column(String(32), default="")
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

    # —— 签收 ——
    # 「已读」是打开消息中心就会置上的，证明不了人真的看到了；需要确认到人的
    # 通知（改集合地点、临时取消）单独要一次签收动作。
    need_ack: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 同一批群发共用一个 batch_id，管理端按批次看签收情况
    batch_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    def out(self) -> dict:
        return {
            "id": self.id, "title": self.title, "content": self.content,
            "template_key": self.template_key, "read": self.read,
            "need_ack": self.need_ack, "acked_at": iso(self.acked_at),
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
