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

import math
import secrets
import string
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, event,
                        inspect as sa_inspect, text)
# 会话类在这里叫 OrmSession：本模块还有一个自己的 Session（登录会话那张表），
# 直接用 Session 这个名字会被它盖掉
from sqlalchemy.orm import (Mapped, Session as OrmSession, mapped_column,
                            object_session, relationship)
from sqlalchemy.orm.attributes import flag_modified

from . import config
from .crypto import EncryptedString
from .database import Base


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def new_checkin_token() -> str:
    """固定的签到码：6 位大写字母数字，能被念、能被抄在纸上。

    跟动态码的 8 位刻意错开长度：两类码在形状上分得开，扫码的人看不出、
    但看日志和聊天记录的人一眼能认出哪串是哪种。
    """
    return "".join(secrets.choice(string.ascii_uppercase + string.digits)
                   for _ in range(6))


def now() -> datetime:
    return datetime.now()


def parse_local_dt(raw) -> datetime:
    """把客户端传来的时间串解析成**本地裸时间**（与 now() 和库里的时间同一口径）。

    全平台的时间都是不带时区的本地时间（容器 TZ=Asia/Shanghai）。客户端要是传了
    带时区的串（2026-09-27T09:00Z、+08:00），fromisoformat 会解出带时区的时间，
    跟库里的裸时间一比较就是 TypeError，接口直接 500。这里先按它自带的时区换算成
    本地时间，再去掉时区；不带时区的原样当本地时间。

    格式不对抛 ValueError，由调用方翻成 400。
    """
    text = str(raw).strip().replace(" ", "T")
    if text.endswith(("Z", "z")):       # Python 3.10 的 fromisoformat 不认 Z
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def like_pattern(text: str) -> str:
    """把搜索框里的一串字变成 LIKE 的匹配串。

    % 与 _ 在 LIKE 里是通配符：直接拼进去，搜一个 % 就会命中全部记录。
    转义掉，让搜索只按字面匹配（调用处要配 escape="\\"）。
    """
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="minutes") if dt else None


# ======================= 用户与会话 =======================

# 用户身份（可多个）：管理员 / 青协 / 活动发起方 / 学生
#
# 「活动发起方」同时盖住学校各部门的老师和院系二级志愿团体的学生负责人：
# 他们做的是同一件事——发起活动、交给校青协审批。类型（老师/学生）另算，
# 决定的是能不能报名，与这里无关。
ROLES = ("admin", "qingxie", "initiator", "student")
ROLE_LABELS = {"admin": "管理员", "qingxie": "青协",
               "initiator": "活动发起方", "student": "学生"}
# 可被设为活动管理者的身份（发起方 / 青协；管理员天然全权）
MANAGER_ROLES = ("initiator", "qingxie")
# 审计 changes 里不对外展示的附注，字段名都以下划线开头（见 audit.log）：
# 以哪个身份操作的；这次改动影响到的人（逗号分隔的用户 id）
IDENTITY_FIELD = "_identity"
AFFECTED_FIELD = "_affected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    gender: Mapped[str] = mapped_column(String(8), default="")
    college: Mapped[str] = mapped_column(String(64), default="")
    grade: Mapped[str] = mapped_column(String(16), default="")       # 如 2023
    # 校区与学段：招募范围、场次与岗位的条件要求都按这两项筛人
    campus: Mapped[str] = mapped_column(String(32), default="")
    stage: Mapped[str] = mapped_column(String(16), default="")       # 本科生/硕士研究生/…
    phone: Mapped[str] = mapped_column(String(32), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    # 学校接口里没有微信号/身份证号，需志愿者自行完善后才能报名
    wechat: Mapped[str] = mapped_column(String(64), default="")
    # 身份证号仅存储不外显（导出表也不含，见 exporter.py）
    # 密文比原文长得多（Fernet 约 140 字符），列宽按密文留
    id_card: Mapped[str] = mapped_column(EncryptedString(255), default="")
    # 港澳台学生与留学生没有内地居民身份证号，勾选后免填
    no_mainland_id: Mapped[bool] = mapped_column(Boolean, default=False)
    # —— 类型与身份 ——
    # 类型二选一（老师/学生）；身份可多个（管理员/青协/活动发起方/学生），可切换视角。
    user_type: Mapped[str] = mapped_column(String(12), default="student")  # teacher / student
    roles: Mapped[list] = mapped_column(JSON, default=list)                # ["student", ...]

    # —— 青协内部层级（只对 qingxie 身份有意义）——
    #   council 会长团：除集成设置外与管理员同权
    #   head    部长：本部门活动全部可见，审本部门的活动，可设本部门的骨干/成员
    #   core    部门骨干：只看自己被加进管理者的活动
    #   member  部门成员：本部门活动只读，可以点、提交时被拦（学习权限）
    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True, index=True)
    qingxie_post: Mapped[str] = mapped_column(String(12), default="member")
    # 部门成员跟着哪位部门骨干学：可见范围与他一致（见 perm.managed_activity_ids）。
    # 只对 qingxie_post="member" 有意义。
    supervisor_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    POST_LABELS = {"council": "会长团", "head": "部长",
                   "core": "部门骨干", "member": "部门成员"}

    # 当前身份视角：本次请求以哪个身份在操作（前端的 X-Identity 头，见
    # deps.current_user）。**普通实例属性，不是列**，不进库、不跨请求。
    # 设了它，下面这组判权方法就只认这一个身份——只会收窄，不会放宽；
    # 没设（定时任务、从库里查出来的别人）就是全部身份的并集。
    # 绝不能为了收窄去改 roles：那是列，改了会随下一次 commit 写进库。
    #
    # 每个判权方法都有 real=True：绕开视角、按真实身份。用在「身份事实」上
    # ——/me 下发的身份、「不能移除自己的管理员身份」、别人能不能值班/当
    # 管理者。注意同一个请求里从库里查到的「本人」就是当前用户这个实例
    # （会话的身份映射），也带着视角，所以判断「目标」的地方一律 real=True。
    _view = None

    def post_label(self) -> str:
        return self.POST_LABELS.get(self.qingxie_post, self.qingxie_post)

    def post(self, *, real: bool = False) -> str | None:
        """青协层级；不是青协身份就没有层级。"""
        return self.qingxie_post if self.has_role("qingxie", real=real) else None

    def is_council(self, *, real: bool = False) -> bool:
        return self.post(real=real) == "council"

    def is_head(self, *, real: bool = False) -> bool:
        return self.post(real=real) == "head"

    def is_trainee(self, *, real: bool = False) -> bool:
        """部门成员：学习权限，任何写操作都会被拦。

        同时持有管理员或活动发起方身份的人不算——那两种身份本身就能写，
        青协层级只描述他在青协里的位置。以青协身份操作时就只是部门成员。
        """
        return (self.post(real=real) == "member" and not self.is_admin(real=real)
                and not self.has_role("initiator", real=real))

    def can_write(self, *, real: bool = False) -> bool:
        """能不能真正改数据。部门成员之外都能。"""
        return not self.is_trainee(real=real)

    def has_full_access(self, *, real: bool = False) -> bool:
        """看得到全部活动、能管全部审批：管理员与会长团。"""
        return self.is_admin(real=real) or self.is_council(real=real)

    def has_role(self, *roles: str, real: bool = False) -> bool:
        held = set(self.roles or [])
        if self._view and not real:
            held &= {self._view}
        return bool(set(roles) & held)

    def is_admin(self, *, real: bool = False) -> bool:
        return self.has_role("admin", real=real)

    def is_manager_identity(self, *, real: bool = False) -> bool:
        """能否进入工作台/被设为活动管理者：发起方 / 青协（管理员天然可以）。"""
        return self.is_admin(real=real) or self.has_role(*MANAGER_ROLES, real=real)

    def set_view(self, identity: str) -> None:
        """采用前端声明的当前身份；自己没有这个身份就忽略（按并集）。"""
        self._view = identity if identity in (self.roles or []) else None

    def view(self) -> str | None:
        return self._view

    def profile_complete(self) -> bool:
        """资料完善度：手机号 + 微信号 + 校区 + 身份证号（老师类型不要求）。

        港澳台学生与留学生勾了「无内地身份证号」的，这一项直接跳过——
        他们本来就开不出这个号，卡着不让报名没有道理。

        校区必填是因为它同时是招募范围的筛选项：留空的人在「限校区」的活动上
        会被静默挡掉，拦下来的话还会显示「校区需为…（您是 未填写）」。与其让
        人对着这么一句发愣，不如在完善资料时就让他选。
        """
        if self.user_type == "teacher":
            return True
        if not ((self.phone or "").strip() and (self.wechat or "").strip()):
            return False
        if not (self.campus or "").strip():
            return False
        return self.no_mainland_id or bool((self.id_card or "").strip())

    def masked_id_card(self) -> str:
        card = (self.id_card or "").strip()
        if len(card) < 8:
            return ""
        return f"{card[:4]}{'*' * (len(card) - 7)}{card[-3:]}"

    def active_role_label(self) -> str:
        if not self.roles:
            # 没有任何身份时按身份类型显示，别把老师叫成「学生」
            return "教职工" if self.user_type == "teacher" else "学生"
        return "、".join(ROLE_LABELS.get(r, r) for r in self.roles)

    def brief(self) -> dict:
        return {
            "id": self.id, "name": self.name, "student_id": self.student_id,
            "gender": self.gender, "college": self.college, "grade": self.grade,
            "campus": self.campus, "stage": self.stage,
            "user_type": self.user_type, "roles": self.roles or [],
            "role_label": self.active_role_label(),
            # 下发的是身份事实，不跟当前视角：前端切身份时要用到全部层级
            "qingxie_post": self.post(real=True),
            "post_label": self.post_label() if self.post(real=True) else "",
            "department_id": self.department_id or "",
            "is_trainee": self.is_trainee(real=True),
        }

    def full(self) -> dict:
        """含联系方式。仅在本人/有权限的后台接口下发。"""
        return {
            **self.brief(),
            "phone": self.phone, "email": self.email, "wechat": self.wechat,
            "no_mainland_id": self.no_mainland_id,
            # 只回脱敏值：让本人看得出「已经填过了」，又不把号原样发出去
            "id_card_masked": self.masked_id_card(),
            "profile_complete": self.profile_complete(),
        }


class Department(Base):
    """校青协的部门。活动审批按部门归口，部长是默认审批人。

    做成表而不是常量：部长每年换届，部门也可能增减；对外联络部还要挂一句
    给二级团体看的说明。
    """
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    note: Mapped[str] = mapped_column(String(128), default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)
    # —— 值班（目前只有对外联络部启用，见 duty.py）——
    # 二级团体的审批量大，部长一个人盯不过来：由骨干轮流值班，审批通知只发给
    # 值班的人。空 = 没人值班，通知回落给部长。
    duty_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 每晚给部长的值班日报发到了哪一天：清扫每分钟一轮、多进程抢主，
    # 落库才能保证一天只发一次（进程重启也不补发第二遍）
    duty_digest_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class DutyHandover(Base):
    """值班交接流水：谁、从谁手里、交给谁、什么时候。

    当前值班人挂在 Department 上，历史留在这里——哪天那条审批为什么没人管，
    事后要查得到当时是谁在班。
    """
    __tablename__ = "duty_handovers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    dept_id: Mapped[str] = mapped_column(ForeignKey("departments.id"), index=True)
    from_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


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
    # 志愿者怎么进来：instant 即选即得 / screened 报名后筛选（见 config.RECRUIT_MODES）
    recruit_mode: Mapped[str] = mapped_column(String(12), default="instant")
    intro: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(String(128), default="")
    # 活动地点在地图上的点 {"lat", "lng"}（WGS-84，和签到点、天地图同一套坐标），
    # 选填。志愿者在活动详情里点「导航」按它跳到地图应用
    location_point: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    # 联系人（多人）：[{"name": 姓名, "phone": 电话}]
    contacts: Mapped[list] = mapped_column(JSON, default=list)
    # 志愿者联络群二维码（dataURL）。必填；不在活动详情公开，报名成功/候补转正/
    # 录取之后弹给本人一次，之后在「我的报名」里随时能再看。复制活动不带它——
    # 每场活动一个新群。
    contact_group_qr: Mapped[str] = mapped_column(Text, default="")
    cover_hue: Mapped[int] = mapped_column(Integer, default=0)           # 封面色相（演示用渐变色卡）
    # 谁办的这场活动：校青协 / 某院系志愿团体 / 学校某部门。必填——
    # 审批时靠它判断该由哪个部门归口，志愿者也要知道自己报的是谁的活动。
    organizer: Mapped[str] = mapped_column(String(64), default="")
    # 联合主办方（选填）：跟别的单位合办时填另一个主办方，对外显示成「A × B」
    # （见 organizer_text）。归口审批、筛选、统计一律只看 organizer——
    # 多了个合办方并不改变这场活动该由谁审、归哪一类。
    co_organizer: Mapped[str] = mapped_column(String(64), default="")
    # 活动级的整体要求与保障。岗位上的「要求原文」讲的是那个岗位特有的事，
    # 这两条讲的是整个活动共通的：着装、集合、餐食、时长、纪念品。
    # 要求选填；保障必填，招募文案里固定有这一段，默认见 config
    volunteer_requirement: Mapped[str] = mapped_column(Text, default="")
    volunteer_benefit: Mapped[str] = mapped_column(
        Text, default=config.DEFAULT_VOLUNTEER_BENEFIT)
    # 招募范围：限定校区、学段与学院，空列表 = 不限。整个活动一刀切，
    # 更细的差别（这一场只要研究生）走场次/岗位各自的条件要求。
    recruit_campuses: Mapped[list] = mapped_column(JSON, default=list)
    recruit_stages: Mapped[list] = mapped_column(JSON, default=list)
    # 学院取值是统一身份认证给的 orgName 原文，要跟 User.college 逐字相等
    recruit_colleges: Mapped[list] = mapped_column(JSON, default=list)

    status: Mapped[str] = mapped_column(String(16), default="draft")
    # draft 草稿 / pending 待审批 / approved 待发布 / published 报名中 /
    # closed 已截止 / finished 已结束 / vb_pending 待外联确认（时长已锁定，
    # 等对外联络部在志愿北京上核对管理者填的活动名称）/ completed 已完成

    # —— 审批 ——
    # 所有活动发布前都要过校青协。默认通过即发布；提交时可以关掉这一项，
    # 那就停在「待发布」，由发起方挑时候自己点。
    approval_dept_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True, index=True)
    approval_assignee_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True)
    approval_comment: Mapped[str] = mapped_column(String(512), default="")
    # 提交审批时勾的：通过即发布。不勾就停在「待发布」，由发起方自己点。
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=True)
    # 置顶（急招）：会长团/管理员点的，有值即置顶，广场上排最前并挂「急招」。
    # 只有报名中的活动能置顶；状态一离开「报名中」就清掉（见下面的
    # _drop_pin_off_published），恢复报名后要急招得再点一次。同时最多
    # config.URGENT_LIMIT 个，见 staff_activities.pin。
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 提醒过一次就记下来，别每分钟催一遍
    approval_reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)

    reg_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reg_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 补记录活动（recruit_mode="record"）自己的活动时间：它没有场次，线下哪天
    # 办的只能直接填。广场不展示它，这两项用在管理端、审批页、时长明细与
    # 提示分（导入即视为参与，加分时间记在活动结束这一刻）。别的活动留空。
    held_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    held_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # —— 志愿时长 ——
    # 核准 = 把这场活动的时长定下来，通知每个人查看，并给一段申诉时间。
    # 截止时没人申诉就自动报校青协审批，通过即锁定，活动落「待外联确认」；
    # 有人申诉就通知管理者，改完再核准一次，来回几次都行，也可以不理会申诉
    # 直接提交审批。
    hours_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
    appeal_deadline: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
    hours_locked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
    # 对外联络部归口的活动，时长报批前要交盖章立项书和盖章结项书。申诉期满时
    # 还没交齐，自动报批就停下来提醒管理者一次；记下时间，别每分钟提醒一遍。
    stamped_reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
    # 志愿北京上对应的活动名称（可以有好几个：分几场建的就填几个）。管理者
    # 提交时长审核时一并填写，时长锁定后推给对外联络部核对。存的是当前这一版，
    # 每次推送的快照与核对结果记在 VbConfirm 上。
    vb_names: Mapped[list] = mapped_column(JSON, default=list)
    # 申诉期满本该自动提交时长审核、却还没填志愿北京活动名称：不提交，提醒
    # 管理者一次（每轮核准一次，同盖章材料）；记下时间，别每分钟提醒一遍
    vb_names_reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True)
    # 志愿北京名称被外联退回、管理者迟迟不重提：每天提醒一次；记的是哪天
    # 提醒过（按天去重，条件更新占位，见 scheduled.remind_vb_resubmit）
    vb_reminded_on: Mapped[date | None] = mapped_column(Date, nullable=True)

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

    STATUS_LABELS = {"draft": "草稿", "pending": "待审批", "approved": "待发布",
                     "published": "报名中", "closed": "已截止",
                     "finished": "已结束", "vb_pending": "待外联确认",
                     "completed": "已完成"}

    def label_status(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)

    def organizer_text(self) -> str:
        """对外显示的组织单位；联合举办时写成「A × B」。

        两侧各自去掉首尾空白：名字里混进空格时，拼出来会多出一个
        「A  × B」，拿去搜索、核对都要对不上。
        """
        a = (self.organizer or "").strip()
        b = (self.co_organizer or "").strip()
        if a and b:
            return f"{a} × {b}"
        return a or b

    def hours_locked(self) -> bool:
        return self.hours_locked_at is not None

    def is_screened(self) -> bool:
        return self.recruit_mode == "screened"

    def is_record(self) -> bool:
        """不公开招募（补记录）：没有场次、不收报名，时长靠导入名单。"""
        return self.recruit_mode == config.RECORD_MODE

    def recruit_label(self) -> str:
        return config.recruit_name(self.recruit_mode)

    def main_shifts(self) -> list["Shift"]:
        """服务场次。培训/彩排不参与名额、时间跨度与广场展示。"""
        return [s for s in self.shifts if s.kind == "service"]

    def trainings(self) -> list["Shift"]:
        """培训/彩排，按开始时间排。"""
        return sorted((s for s in self.shifts if s.kind != "service"),
                      key=lambda s: s.start_time)

    def time_span(self) -> tuple[datetime, datetime] | None:
        """活动整体起止 = 最早服务场次开始 / 最晚服务场次结束。

        培训与彩排常常排在正式服务前一周，算进来的话广场上的活动时间会
        从彩排那天开始，学生以为要连着来好几天。
        """
        if self.is_record():
            # 补记录活动没有场次，活动时间是建活动时直接填的
            if self.held_start and self.held_end:
                return self.held_start, self.held_end
            return None
        rows = self.main_shifts()
        if not rows:
            return None
        return (min(s.start_time for s in rows),
                max(s.end_time for s in rows))

    def last_shift_start(self) -> datetime | None:
        """报名截止最晚只能填到这里：最后一个服务场次的开始时间。

        再往后填是一段死窗口——每个场次在自己开场时就不收人了（见
        enroll_logic.enroll_deadline），最后一场都开始之后，这条活动
        已经一个人也报不进来，页面上却还挂着「报名中」。
        """
        rows = self.main_shifts()
        return max(s.start_time for s in rows) if rows else None


@event.listens_for(Activity.status, "set")
def _drop_pin_off_published(act: Activity, value, _old, _initiator) -> None:
    """状态改成「报名中」以外的任何值，急招置顶随之取消。

    挂在属性上而不是写在各个截止入口里：手动截止、定时任务自动截止，以及
    以后任何把活动挪出「报名中」的地方都走这一处。要是置顶留着，截止了的
    活动仍占一个急招名额，恢复报名时又会不经会长团重新挂上「急招」。
    """
    if value != "published":
        act.pinned_at = None
        # 内存里本来就是 None 时赋值不算改动、不进 UPDATE。并发时别的事务可能
        # 刚提交了置顶，这里照样要把库里那一列清掉
        flag_modified(act, "pinned_at")


class ApprovalEvent(Base):
    """审批流水：提交、转交、通过、驳回各一条，按时间排。

    当前状态挂在 Activity 上（一条活动只有一条「正在进行的审批」），
    历史留在这里——转交给了谁、为什么转，事后要说得清。
    """
    __tablename__ = "approval_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))   # submit/transfer/approve/reject
    actor_id: Mapped[str] = mapped_column(String(32))
    dept_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    comment: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    KIND_LABELS = {"submit": "提交审批", "transfer": "转交",
                   "approve": "通过", "reject": "驳回"}

    def kind_label(self) -> str:
        return self.KIND_LABELS.get(self.kind, self.kind)


class ActivityManager(Base):
    """活动管理者（多人共管）。

    · 创建者自动成为管理者，且**不可被移除**（created_by 只是记录信息，
      权限上不特殊）；
    · 管理者之间权限完全相同，任何管理者可添加/移除其他管理者；
    · duties 只管**通知发给谁**，不限制谁能做什么：一场活动十几个管理者，
      时长申诉没必要惊动管服装的人。留空 = 什么通知都收。
    · 被添加者必须持有 活动发起方/青协 身份（管理员无需添加，天然可见全部）。
    """
    __tablename__ = "activity_managers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    added_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duties: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    DUTIES = {"roster": "志愿者管理", "checkin": "签到", "attire": "志愿服装",
              "hours": "志愿时长"}


def span_text(start: datetime, end: datetime) -> str:
    """一段时间的说法：「09-20 09:00-12:00」，跨天就把两个日期都写上。

    场次和「实际签到签退那一段」共用它。同一段服务在两处各写各的格式，
    对账时就得靠眼睛去认它们说的是同一件事。
    """
    if start.date() == end.date():
        return f"{start:%m-%d} {start:%H:%M}-{end:%H:%M}"
    return f"{start:%m-%d %H:%M} ~ {end:%m-%d %H:%M}"


class Shift(Base):
    """场次：一段服务时间。**时间只存在于这一层**，用于时段冲突检测。

    场次下挂具体岗位。两种模式：
      pick    报名即选岗位：名额与候补都在岗位上
      assign  截止后分配：报名只锁时段，名额与候补在场次上，
              截止后由一键分配把人分进岗位

    培训与彩排也是场次（kind=training/rehearsal），挂在整个活动上：
    applies_to_positions 圈出要来的岗位，可以跨场次（检录组周五、周六两天都
    来同一场培训），空 = 本活动所有报上名的人。报上任一个被圈到的岗位就连带
    报上，最后一个被圈到的报名退掉或调走才连带退；自己不设名额与候补。这样
    签到码、到岗大屏、时长计算、时段冲突检测全部原样复用——它们本来就是按
    场次算的。
    """
    __tablename__ = "shifts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    location: Mapped[str] = mapped_column(String(128), default="")
    mode: Mapped[str] = mapped_column(String(12), default="pick")
    # —— 培训/彩排 ——
    kind: Mapped[str] = mapped_column(String(12), default="service")
    # 培训/彩排只叫这些岗位的人（可以跨场次）；空 = 本活动所有报上名的人。
    # 东门组周五彩排、检录组周六彩排是常事，一刀切会把人叫到不该来的场
    applies_to_positions: Mapped[list] = mapped_column(JSON, default=list)
    # 时长怎么认：checkin 签退−签到 / fixed 到场即按场次时长 / none 不计时长。
    # 把不确定性收在建场次这一次决定里，而不是散在事后逐条核对里。
    hours_mode: Mapped[str] = mapped_column(String(12), default="checkin")
    # 本场次的硬性条件 {标签: 值列表}，空 = 不限。与岗位上的条件同一套匹配
    # 规则（见 enroll_logic.unmet_for_tags），报名时直接拦截。
    requirement_tags: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(512), default="")
    # 管理者暂停这一场的报名（调整岗位用）：不收新报名、新申请、新候补，
    # 候补也不自动顶上，期间名单不动；已报的人可以退出，不记录。恢复时按
    # 候补顺序补齐空位。不用进编辑态就能切
    enroll_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    # —— 名单锁定（见 enroll_logic.shift_locked）——
    # 开场前 lock_hours 小时自动锁定，0 = 不自动锁定，只靠手动。
    # 锁定后不收新报名、新候补，候补不自动顶上（管理者手动转正），
    # 已报的人退出记一次「锁定后退出」。
    lock_hours: Mapped[int] = mapped_column(Integer, default=config.LOCK_HOURS_DEFAULT)
    # 手动锁定，或改 N 时落实下来的锁定
    list_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # 自动锁定已经生效过：管理者过了锁定点再手动解锁，不会被自动锁回去
    auto_lock_spent: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    # 签到提醒只发一次，发过就记时间（定时任务与手动按钮共用这个标记）
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 人员变动日报发到哪一天了，防止一天推多条
    digest_sent_on: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    activity: Mapped[Activity] = relationship(back_populates="shifts")
    positions: Mapped[list["Position"]] = relationship(
        back_populates="shift", order_by="Position.sort",
        cascade="all, delete-orphan")

    MODE_LABELS = {"pick": "报名即选岗位", "assign": "截止后统一分配"}
    KIND_LABELS = {"service": "服务", "training": "培训", "rehearsal": "彩排"}
    HOURS_LABELS = {"checkin": "扫码计时（签退−签到）",
                    "fixed": "固定计时（到场即按场次时长）",
                    "none": "不计时长（只记出勤）"}

    def kind_label(self) -> str:
        return self.KIND_LABELS.get(self.kind, self.kind)

    def hours_label(self) -> str:
        return self.HOURS_LABELS.get(self.hours_mode, self.hours_mode)

    def is_attached(self) -> bool:
        """培训/彩排：跟着服务场次的报名走，自己不招人。"""
        return self.kind != "service"

    def span_text(self) -> str:
        return span_text(self.start_time, self.end_time)

    def mode_label(self) -> str:
        return self.MODE_LABELS.get(self.mode, self.mode)

    def needs_assign(self) -> bool:
        return self.mode == "assign"

    def total_capacity(self) -> int:
        """场次总名额（招多少人）。

        「报名即选岗位」：各岗位名额之和。
        「截止后统一分配」：占位岗位的名额——它就是这一场的招募人数。场次下
        另建的真实岗位是分配用的坑位，人从占位岗位挪过去，仍在这一场里，
        把它们的名额再加进来就重复算了。
        """
        if self.needs_assign():
            holder = self.placeholder()
            return holder.quota if holder else 0
        return sum(p.quota for p in self.positions)

    def placeholder(self):
        """占位岗位：「截止后统一分配」场次里装「还没安排的人」的桶。

        建 assign 场次时自动生成，学生报名都落在它上面；名额记在它头上，但按
        整场数人（见 enroll_logic.has_room）——分到真实岗位的人仍占着这一场的
        名额。人只从它里面往外挪（一键分配、导入、调岗），一键分配不把它当作
        分配目标，排不下的自然留在里面。只有 assign 场次有它。
        """
        return next((p for p in self.positions if p.is_placeholder), None)

    def real_positions(self) -> list["Position"]:
        """真正的岗位，不含占位。一键分配只往这些里面排。"""
        return [p for p in self.positions if not p.is_placeholder]


class Position(Base):
    """岗位：场次下的具体职责。没有自己的时间——时间统一由场次负责，
    同场次内的岗位互斥（一人只占一个），所以换岗不会产生新的时段冲突。"""
    __tablename__ = "positions"
    # 占位岗位的名字；排班表里没分到岗位的人也写这三个字，导入排班按它认回来
    PLACEHOLDER_NAME = "待分配"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    shift_id: Mapped[str] = mapped_column(ForeignKey("shifts.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    location: Mapped[str] = mapped_column(String(128), default="")   # 空 = 用活动地点
    quota: Mapped[int] = mapped_column(Integer, default=1)          # 至少 1
    waitlist_quota: Mapped[int] = mapped_column(Integer, default=3)
    # 硬性条件 {标签: 值或值列表}，空 = 不限。报名时直接拦截，
    # 一键分配时同样作为硬约束（见 assign.py）。
    requirement_tags: Mapped[dict] = mapped_column(JSON, default=dict)
    # 三段给志愿者看的多行文字：服务任务（做什么，必填）/ 岗位要求（对人的
    # 要求，选填）/ 培训内容（到岗前要知道的，选填）。「岗位要求」是文字说明，
    # 与 requirement_tags 那种系统拿去拦人的硬条件不是一回事。
    service_task: Mapped[str] = mapped_column(Text, default="")
    position_requirement: Mapped[str] = mapped_column(Text, default="")
    training_note: Mapped[str] = mapped_column(Text, default="")
    # 需要组长：由管理者在名单里指定；报名时志愿者可表态愿意担任，供参考
    needs_leader: Mapped[bool] = mapped_column(Boolean, default=False)
    # 组长任务：组长要带队做什么（带队进场、清点人数……）。勾了「需要组长」
    # 的岗位必填，指定组长时随任命通知一起发给他
    leader_task: Mapped[str] = mapped_column(String(256), default="")
    # 志愿服装：none 不需要 / issued 统一发放（进领取归还台账）/ own 志愿者自备。
    # 后两种必须带备注（发放地点时间，或自备的样式要求），报名时就要让人看到。
    attire: Mapped[str] = mapped_column(String(8), default="none")
    attire_note: Mapped[str] = mapped_column(String(256), default="")
    # 报酬方式：默认只记志愿时长；勾了「可选劳务」的岗位，志愿者在核准后
    # 确认时长时可以选劳务，选了平台就不记这次时长。备注写标准与发放方式。
    pay_optional: Mapped[bool] = mapped_column(Boolean, default=False)
    pay_note: Mapped[str] = mapped_column(String(256), default="")
    # 岗位组：同一场次内组名相同的岗位共用一名组长（如「东门片区」下的
    # 引导/检录/医疗点）。留空 = 该岗位自成一组。
    group_name: Mapped[str] = mapped_column(String(64), default="")
    # 占位岗位：「截止后统一分配」场次自动建的那一个，见 Shift.placeholder()
    is_placeholder: Mapped[bool] = mapped_column(Boolean, default=False)
    # 暂停这个岗位的报名，口径同 Shift.enroll_paused
    enroll_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    # 手动锁定这个岗位的名单，口径同场次锁定；岗位没有自动锁定
    list_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)

    shift: Mapped[Shift] = relationship(back_populates="positions")

    ATTIRE_LABELS = {"none": "不需要", "issued": "统一发放", "own": "志愿者自备"}

    def attire_label(self) -> str:
        return self.ATTIRE_LABELS.get(self.attire, self.attire)

    def attire_text(self) -> str:
        """给志愿者看的一句话：方式 + 备注；不需要服装时为空。"""
        if self.attire == "none":
            return ""
        return self.attire_label() + (f"，{self.attire_note}" if self.attire_note else "")

    def pay_text(self) -> str:
        """报酬方式一句话；只记时长的岗位为空。"""
        if not self.pay_optional:
            return ""
        return "可选劳务" + (f"，{self.pay_note}" if self.pay_note else "")

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
    # shift / position：学习材料；application：报名问卷里志愿者上传的文件；
    # proposal / stamped：立项书与盖章立项书（owner_id = 活动 id，各留一份）
    owner_type: Mapped[str] = mapped_column(String(12))
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


class VbConfirm(Base):
    """志愿北京名称的外联确认：时长锁定后，把管理者填的志愿北京活动名称推给
    对外联络部，值班人（没人值班是部长）上志愿北京核对。

    一次推送一条：时长审核通过时推一次（kind=lock）；外联退回、管理者改了
    名称重提再推一次（kind=resubmit，时长保持锁定，不重走时长审核）。
    确认 = 活动落「已完成」；退回必须写理由，发给管理者。时长解锁时没处理完
    的和已确认的一并作废（void）——改过的时长要重新锁定、重新推送。
    记录都留着，审核页签里就是这场活动的全部往来。
    """
    __tablename__ = "vb_confirms"

    STATUS_LABELS = {"pending": "待外联确认", "confirmed": "已确认",
                     "returned": "已退回", "void": "已作废"}
    KIND_LABELS = {"lock": "时长锁定后推送", "resubmit": "管理者重提"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12), default="lock")
    names: Mapped[list] = mapped_column(JSON, default=list)   # 这一次推送的名称快照
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    submitted_by: Mapped[str] = mapped_column(String(32))   # 推送时拍板的人 / 重提的管理者
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    handled_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reply: Mapped[str] = mapped_column(String(512), default="")
    # 放太久没人确认就催外联一次，催过记下时间，只催这一次
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def status_label(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)

    def kind_label(self) -> str:
        return self.KIND_LABELS.get(self.kind, self.kind)


# ======================= 报名后筛选：报名问卷与申请 =======================

class FormQuestion(Base):
    """报名问卷里的一道题。一个活动一份表，题目按 sort 排。"""
    __tablename__ = "form_questions"

    KINDS = {"single": "单选", "multi": "多选", "text": "填空", "file": "文件上传"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(200))
    options: Mapped[list] = mapped_column(JSON, default=list)   # 单选/多选的选项
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    # 显示条件 {"question_id": 前面某道单选/多选题, "values": [选中其一才显示这道]}。
    # 空 = 一直显示。条件不成立时这道题不显示、不校验、也不存答案。
    show_if: Mapped[dict] = mapped_column(JSON, default=dict)
    # 适用岗位：只有选了其中某个岗位的人才要答这道题。空 = 人人都答。
    # 和场次的 applies_to_positions 一个意思，只是那边管培训、这边管题目。
    applies_to_positions: Mapped[list] = mapped_column(JSON, default=list)
    sort: Mapped[int] = mapped_column(Integer, default=0)

    def out(self) -> dict:
        return {"id": self.id, "kind": self.kind, "kind_label": self.KINDS.get(self.kind, self.kind),
                "title": self.title, "options": self.options or [],
                "required": self.required, "show_if": self.show_if or {},
                "applies_to_positions": self.applies_to_positions or [],
                "sort": self.sort}


class Application(Base):
    """一个人对一个活动的申请，一活动只填一次。

    意向场次不放在这里：申请时按每个意向场次各写一条 status=applied 的
    Enrollment——时段冲突、名单、录取都沿用报名那一套，不必另起一套账。
    意向**岗位**放在这里：一个场次可以勾好几个岗位，Enrollment 上只放得下一个。
    """
    __tablename__ = "applications"
    __table_args__ = (Index("uq_application", "activity_id", "user_id", unique=True),)

    STATUS_LABELS = {"applied": "待筛选", "accepted": "已录取",
                     "rejected": "未录取"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)   # 题目 id → 答案
    # 勾中的意向岗位（跨场次，可多个）。每个意向场次的 Enrollment 上只放得下
    # 一个 position_id，同场次勾了好几个岗位时，完整的意向记在这里，
    # 录取时管理者从中挑一个。
    wanted_positions: Mapped[list] = mapped_column(JSON, default=list)
    # 表态愿意当组长，录取时带到报名记录上；只是给管理者的参考
    leader_willing: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12), default="applied", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def status_label(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)


# ======================= 报名 / 候补 / 分配 =======================

class Enrollment(Base):
    """一条报名 = （用户 × 场次）。一人对同一场次只有一条有效记录，
    也就是同场次下只能占一个岗位——候补和正式都算。

    status 流转：
      applied          报名后筛选：已申请这一场，等管理者挑（时段照样算占用）
      enrolled         正式报名（即选即得 / 候补转正确认 / 筛选录取 / 管理员添加）
      waitlisted       候补排队（按 joined_at 先后）
      pending_confirm  候补转正，等待用户在 confirm_deadline 前确认
      cancelled        已退选（cancel_type: normal 正常 / locked 名单锁定后退出 /
                                     expired 放弃候补超时 / abandoned 主动放弃候补 /
                                     rejected 未录取 /
                                     not_chosen 录取时没选中的意向场次）
    """
    __tablename__ = "enrollments"
    __table_args__ = (
        # 一人在一个场次只能有一条有效记录。
        # 这是并发抢报的兜底：哪条路径漏了加场次锁也插不进第二条。
        Index("uq_enrollment_live", "shift_id", "user_id", unique=True,
              postgresql_where=text(
                  "status IN ('applied', 'enrolled', 'waitlisted', 'pending_confirm')")),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    shift_id: Mapped[str] = mapped_column(ForeignKey("shifts.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)

    status: Mapped[str] = mapped_column(String(24), default="enrolled", index=True)
    cancel_type: Mapped[str] = mapped_column(String(16), default="")
    # 联络群二维码只弹一次：报名成功/转正/录取后弹过就记下来，之后在
    # 「我的报名」里自己点开看

    # 是否接受管理者把自己调到别的岗位。**只是参考**——管理者依然可以调，
    # 界面上会提示「本人不接受调剂」。报名时默认勾上，候补也一样由本人决定。
    allow_reassign: Mapped[bool] = mapped_column(Boolean, default=True)

    # —— 组长 ——
    # 报名时表态愿意担任。只是给管理者看的参考，不决定谁当选。
    leader_willing: Mapped[bool] = mapped_column(Boolean, default=False)
    # 即选即得活动的报名问卷答案（题目 id → 答案）；报名后筛选的答案在 Application 上
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    is_leader: Mapped[bool] = mapped_column(Boolean, default=False)

    position_id: Mapped[str | None] = mapped_column(
        ForeignKey("positions.id"), nullable=True, index=True)
    assign_forced: Mapped[bool] = mapped_column(Boolean, default=False)  # 违规强排标记
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    assigned_by: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # —— 请假（只用于培训/彩排这类附属场次）——
    # 不动 status：status 被名额、候补、签到、时长一路过滤着，加一个值要改
    # 十几处判断；请假的人仍然是「已报名」，只是这一场不来。
    excused: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    excuse_reason: Mapped[str] = mapped_column(String(256), default="")
    excused_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # self 自己报名 / admin 负责人补招 / party_school 补招的党校同学（见 hours.PARTY_SCHOOL）
    source: Mapped[str] = mapped_column(String(16), default="self")
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=now,
                                                index=True)  # 排队/报名时间
    # 退选时间。和 joined_at 一起就是「报了多久之后退的」——退选画像要这个，
    # 从留痕里按文本翻是翻得出来，但那不是能拿来统计的东西
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirm_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def active(self) -> bool:
        return self.status in ("enrolled", "waitlisted", "pending_confirm")


# ======================= 签到 =======================

class CheckinTask(Base):
    """签到任务：整场次一个统一码，或每个岗位一个独立码。

    token 是**固定码**——建任务时生成、一直在册，可以打印出来贴到现场。
    日常用的动态码不落这张表，它只活在 Redis 里（见 locks.py 那一节）。
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

    def window(self, shift: Shift) -> tuple[datetime, datetime]:
        """实际生效的可签时段 (起, 止)。签到、签退都只在这段时间里、且任务开着时能做。

        起止在库里都该有值：生成时默认取场次起止各外扩
        CHECKIN_WINDOW_PAD_MINUTES，改时段也不许留空。列本身允许为空，万一碰上
        空值就按生成时的默认值补上。签退截止
        必须是个确定的时刻：过了它还没签退的人按场次结束时间兜底（见
        routers/staff_ops.effective_checkout），「不限」就永远判不出来。
        """
        pad = timedelta(minutes=config.CHECKIN_WINDOW_PAD_MINUTES)
        return (self.valid_from or shift.start_time - pad,
                self.valid_to or shift.end_time + pad)


class CheckinGeo(Base):
    """定位签到的坐标锚点：**这张表有行 = 这个任务改用定位签到**，没有 = 扫码。

    单独一张表而不是给 checkin_tasks 加列：线上没有 Alembic，
    `create_all()` 只建缺失的表，不会给已有的表加列（见 database.py）。
    加表当场就能用，加列得手工去生产库上 ALTER。

    锚点最好由管理者站在现场用手机量（「使用我的当前位置」），那样它的可信度
    等于「管理者的手机可信」；在地图上点的也收，但审计里记着来源（见
    routers/staff_ops.set_geo）。志愿者那一端传上来的坐标只用来比对，不采信
    任何客户端自己算出来的结论。
    """
    __tablename__ = "checkin_geos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("checkin_tasks.id"),
                                         unique=True, index=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[int] = mapped_column(Integer, default=config.GEO_RADIUS_DEFAULT_M)
    # 量锚点的那一刻手机报的误差，只用于展示（让管理员知道这个点准不准）
    accuracy_m: Mapped[float] = mapped_column(Float, default=0)
    set_by: Mapped[str] = mapped_column(String(32), default="")
    set_at: Mapped[datetime] = mapped_column(DateTime, default=now)


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


# ======================= 时长 / 审计 / 通知 / 集成配置 =======================

def day_of(moment: datetime) -> datetime:
    """那一刻所在的那一天（本地 0 点）。

    段上的服务日期按天记：同一天里上午场还是下午场，看 time_text。
    """
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def part_is_pay(kind: str, reward: str) -> bool:
    """这一段按不按劳务计。

    只有签到算出来的段能选劳务（岗位开了「可选劳务」，本人才选得了）；手动加/扣
    与名单导入的段只记志愿时长。
    """
    return kind == "shift" and reward == "pay"


def segment_out(fields: dict) -> dict:
    """一段在接口里的样子：前端一直在读的那套键名（time / position）加服务日期。

    键取列名（time_text / position_name）。算出来还没落库的那几段也走这里，
    预览与库里的明细才不会在键名上各说各的。
    """
    out = {"id": fields.get("id", ""), "label": fields.get("label", ""),
           "time": fields.get("time_text", ""),
           "position": fields.get("position_name", ""),
           "hours": fields.get("hours", 0.0), "kind": fields.get("kind", ""),
           "shift_id": fields.get("shift_id"),
           "position_id": fields.get("position_id"),
           "date": f"{fields['date']:%Y-%m-%d}" if fields.get("date") else ""}
    if part_is_pay(out["kind"], fields.get("reward", "")):
        out["reward"] = "pay"
    return out


class HoursSegment(Base):
    """一笔时长里的一段明细。

    一段一行，不是一个 JSON 数组：按服务日期、按活动、按人统计都能交给带索引的
    SQL，不必把整份明细拉进 Python 再拆。五种：
      shift     签到结算按场次一段：哪一场、什么时段、什么岗位
      manual    管理者手动加/扣一段：备注就是 label，扣减记负数
      import    补记录活动导入名单一段：时段写活动时间
      override  管理者核定时调整：核定的数与其余各段结算之差，一笔最多一段
      appeal    申诉改判：改判的数与其余各段结算之差，一笔最多一段
    override 与 appeal 是人判出来的结果，不能在明细里单独删：删了就等于悄悄推翻
    那次核定或改判。要改就重新核定、重新判。

    段是账的依据：hours 记原始值，一笔的合计恒等于各段之和（见
    _sync_hours_from_segments）。取整到 0.5 只发生在对外结算那一步（见
    HoursRecord.split），段上的数不动——对账时明细和合计才对得上。
    """
    __tablename__ = "hours_segments"

    # 定长列：这几项都是从别处抄来的（场次名、岗位名、备注），抄来的可能比列宽
    # 长。收窄放在 normalize 一处，写段的路径不必各记各的宽度
    WIDTHS = {"label": 128, "position_name": 64, "time_text": 64}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # 记录没了段跟着没：外键的 ON DELETE CASCADE 之外，代码里删记录也靠
    # relationship 的 delete-orphan 一起带走。留下孤儿段，统计会把它算进去，
    # 直接污染时长
    record_id: Mapped[str] = mapped_column(
        ForeignKey("hours_records.id", ondelete="CASCADE"), index=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # shift/manual/import/override/appeal
    # 同一笔内的顺序：写入时取这一笔当前的最大值 + 1，明细的顺序就是写入的顺序
    seq: Mapped[int] = mapped_column(Integer, default=0)
    # 服务日期（本地 0 点）：这一段算哪一天的服务，不是录进来的那天
    date: Mapped[datetime] = mapped_column(DateTime)
    hours: Mapped[float] = mapped_column(Float)         # 原始值，不取整；手动扣减为负
    reward: Mapped[str] = mapped_column(String(8), default="")   # "" / "pay"
    label: Mapped[str] = mapped_column(String(128), default="")
    position_name: Mapped[str] = mapped_column(String(64), default="")
    time_text: Mapped[str] = mapped_column(String(64), default="")
    shift_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    position_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    @staticmethod
    def normalize(fields: dict) -> dict:
        """段字段的收尾：定长列收窄，小时数写到两位。

        小时数不取整到 0.5——段上留原始值，取整是结算那一步的事。写到两位是为了
        合计与明细在界面上对得上：二进制存不下 0.1 这种数，不收一下，相加会多出
        0.0000000000000004 的尾巴。
        """
        out = dict(fields)
        for name, width in HoursSegment.WIDTHS.items():
            if name in out:
                out[name] = (out[name] or "")[:width]
        if "hours" in out:
            out["hours"] = round(float(out["hours"]), 2)
        return out

    def is_pay(self) -> bool:
        return part_is_pay(self.kind, self.reward)

    def refill(self, **fields) -> None:
        """按现算的结果改写这一段：收尾同 add_segment，顺序（seq）不动。"""
        for name, value in self.normalize(fields).items():
            setattr(self, name, value)

    def out(self) -> dict:
        return segment_out({"id": self.id, "kind": self.kind, "reward": self.reward,
                            "date": self.date, "hours": self.hours,
                            "label": self.label, "time_text": self.time_text,
                            "position_name": self.position_name,
                            "shift_id": self.shift_id,
                            "position_id": self.position_id})


class HoursRecord(Base):
    """一个人在一场活动里的志愿时长。

    时长就存在本平台，不外发。核准与锁定记在活动上（hours_approved_at /
    appeal_deadline / hours_locked_at）：一场活动的账一起结，不必逐条记状态。

    一笔账由若干段（HoursSegment）组成：hours 恒等于各段之和（原始值）；对外
    结算的两半（志愿时长 / 劳务，各自向上取整到 0.5）见 split()。
    """
    __tablename__ = "hours_records"
    __table_args__ = (
        # 一场活动一个人只有一笔：从代码约定变成数据库保证。并发下两条写入路径
        # 同时发现「他还没有记录」时，也只会有一条落库
        UniqueConstraint("activity_id", "user_id",
                         name="ux_hours_record_user_activity"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    # 各段之和（原始值）：由 _sync_hours_from_segments 在一处写，别处不要直接改。
    # 默认 0 就是「一段都还没有」时的和，建了记录、段还没加上去也不会是空值
    hours: Mapped[float] = mapped_column(Float, default=0.0)
    # checkin 签到统计 / manual 手动添加 / import 补记录活动导入名单
    source: Mapped[str] = mapped_column(String(12), default="checkin")
    note: Mapped[str] = mapped_column(String(256), default="")            # 手动添加时的备注
    confirmed_by: Mapped[str] = mapped_column(String(32), default="")
    # 志愿者本人的确认：核准后到申诉截止之间，本人看过说没问题，并给每一段
    # 选定报酬方式（见 HoursSegment.reward）。到点没表态的按「没问题 + 全部志愿
    # 时长」处理，锁定时补上确认时间。
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 上一次核准时通知本人的数值：再次核准只惊动数值变过、或申诉过的人
    approved_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    # 明细：按写入顺序（seq）排。记录一删段跟着删——留下的孤儿段会被统计算进去
    segments: Mapped[list["HoursSegment"]] = relationship(
        order_by=lambda: (HoursSegment.seq, HoursSegment.id),
        cascade="all, delete-orphan")

    REWARD_LABELS = {"hours": "志愿时长", "pay": "劳务"}

    def live_segments(self) -> list[HoursSegment]:
        """这一笔现在的段：本事务里已经删掉的那些不算。

        会话把删掉的对象留在集合里（delete 之后没 flush 的、已经 flush 过的都在），
        读的时候不排掉它，合计数会多、差额也会按一段已经不存在的时长去补
        （见 set_total）。删段一律走 drop_segment（它把段从集合里摘掉），这里再
        兜一道，别的写法也错不了账。
        """
        db = object_session(self)
        if db is None:
            return list(self.segments)
        gone = {s.id for s in db.deleted if isinstance(s, HoursSegment)}
        return [s for s in self.segments
                if s.id not in gone and not sa_inspect(s).deleted]

    # —— 写段 ——

    def add_segment(self, **fields) -> HoursSegment:
        """往这一笔里加一段。

        seq 接着这一笔当前的最大值排：明细的顺序就是写入的顺序。activity_id 与
        user_id 从这一笔上取，段和它挂的记录不会各说各的。
        """
        seg = HoursSegment(
            id=uid("seg"), record_id=self.id, activity_id=self.activity_id,
            user_id=self.user_id,
            seq=max((s.seq for s in self.live_segments()), default=0) + 1,
            **HoursSegment.normalize(fields))
        self.segments.append(seg)
        return seg

    def drop_segment(self, seg: HoursSegment) -> None:
        """删掉一段。

        先从集合里摘掉，再交给会话 delete。摘掉是为了本事务后面几次「这一笔
        现在是多少」都算不到它（差额、合计都按剩下的段算）；交给会话是为了
        session.deleted 里留着它的痕迹——合计重算那一处据此把它排除
        （见 _sync_hours_from_segments），只摘不删的话那处不知道段动过。
        """
        if seg in self.segments:
            self.segments.remove(seg)
        db = object_session(self)
        if db is not None:
            db.delete(seg)

    def settle_to(self, target: float, *, kind: str, label: str,
                  date: datetime) -> None:
        """把这一笔的结算合计定成 target（先向上取整到 0.5）：差额记在 kind 那一段上。

        管理者核定时改的数、申诉改判的数都是「这一笔就是这么多」，不是「再加
        这么多」，所以同一种调整段只留一段：按差额改写它，差额为 0 就删掉。
        要是不这样，同一笔每改一次就多一段，账越点越大；核定改回签到算出的原数
        时，上一次的调整也去不掉。

        差额按结算口径算（其余各段两半各自取整后的和），不按原始值算：差额是
        0.5 的整数倍，加到志愿那一半上，取整后的合计才恰好等于 target。调用方
        先把劳务选择清掉（clear_pay），差额落在志愿时长上不会把哪一半扣成负数。
        """
        segs = self.live_segments()
        seg = next((s for s in segs if s.kind == kind), None)
        other = self.settle((s.hours, s.is_pay()) for s in segs if s is not seg)
        delta = round(self.ceil_half(target) - sum(other), 2)
        if seg is None:
            if delta:
                self.add_segment(kind=kind, date=date, hours=delta, label=label,
                                 time_text=f"{date:%m-%d}")
            return
        if delta:
            seg.refill(hours=delta, date=date, label=label,
                       time_text=f"{date:%m-%d}")
        else:
            self.drop_segment(seg)

    # —— 结算 ——

    @staticmethod
    def sum_segments(segments) -> float:
        """各段之和（原始值）。

        收到两位不是取整到 0.5：段上的数就写到两位，直接相加会因二进制表示多出
        0.0000000000000004 这种尾巴，界面上的合计与明细就各显示各的。
        """
        return round(sum(float(s.hours or 0) for s in segments), 2)

    @staticmethod
    def ceil_half(value: float) -> float:
        """向上取整到 0.5。

        取整前先把「两倍」收到六位小数：二进制存不下 0.1 这种数，两段 2.5 与 2
        相加会算出 4.500000000000001，直接向上取整就平白多出 0.5。段上的数都
        写到两位，收六位碰不到真值。
        """
        return math.ceil(round(value * 2, 6)) / 2

    @staticmethod
    def settle(items) -> tuple[float, float]:
        """一笔账拆成（志愿时长, 劳务）：两半各自向上取整到 0.5，再相加。

        items 是 (原始小时数, 是否劳务) 的序列。管理端那张现算的总表还没落库，
        也走这里：取整的规则只此一处，界面上看到的和导出去的才对得上。两半之和
        可能略大于原始合计，这是有意的——0.5 是能报给志愿北京的最小刻度。
        """
        rows = list(items)        # 两半各要过一遍，生成器只能取一次
        return HoursRecord.settle_sums(sum(h for h, pay in rows if not pay),
                                       sum(h for h, pay in rows if pay))

    @staticmethod
    def settle_sums(volunteer: float, paid: float) -> tuple[float, float]:
        """两半的原始和 → 结算的（志愿时长, 劳务）。

        扣减段只记志愿时长，本人又把签到段都选成劳务时，志愿那一半的原始和会
        是负数：不足的部分从劳务里扣，志愿时长记 0。一笔的原始合计不会是负的
        （扣减与删段都拦着），这样两半都不会出现负数。按段在 SQL 里求和的统计
        也走这里，界面、导出与统计才是同一套数。
        """
        if volunteer < 0:
            volunteer, paid = 0.0, max(0.0, paid + volunteer)
        return HoursRecord.ceil_half(volunteer), HoursRecord.ceil_half(paid)

    def split(self) -> tuple[float, float]:
        """这一笔拆成（志愿时长, 劳务）。"""
        return self.settle((s.hours, s.is_pay()) for s in self.live_segments())

    def counted_hours(self) -> float:
        """计入志愿时长的那部分。"""
        return self.split()[0]

    def pay_hours(self) -> float:
        """按劳务计、不记时长的那部分。"""
        return self.split()[1]

    def settled_hours(self) -> float:
        """结算合计：两半各自取整到 0.5 之后的和。对外显示、导出、锁定都用它。"""
        return sum(self.split())

    def parts_text(self, *, hours_only: bool = False) -> str:
        """明细拼成一行：「09-20 08:00-12:00 东门引导；09-21 …」，不换行。
        hours_only：只拼计志愿时长的那几段（导出给志愿北京的备注用）。"""
        return "；".join(" ".join(x for x in (s.time_text,
                                              s.position_name or s.label) if x)
                         for s in self.live_segments()
                         if not (hours_only and s.is_pay()))

    def reward_label(self) -> str:
        counted, pay = self.split()
        if pay <= 0:
            return "志愿时长"
        if counted <= 0:
            return "劳务"
        return f"志愿时长 {counted:g}h + 劳务 {pay:g}h"

    def clear_pay(self) -> None:
        """各段都退回志愿时长：该笔所有段的报酬方式一起清掉。

        到点没表态时这样结；核准后这一笔的数被改了也这样清——本人是对着旧数选
        的劳务，新数得重新确认、重新选。"""
        for s in self.live_segments():
            s.reward = ""


def _segment_record(session: OrmSession, seg: HoursSegment) -> HoursRecord | None:
    """这一段挂在哪一笔上。

    还没落库的那一笔也在 session.new 里：先扫它，不然会拿着一个库里还不存在的
    主键去查，查出来是空的，这一段就没人算进合计。
    """
    if not seg.record_id:
        return None
    for obj in session.new:
        if isinstance(obj, HoursRecord) and obj.id == seg.record_id:
            return obj
    return session.get(HoursRecord, seg.record_id)


@event.listens_for(OrmSession, "before_flush")
def _sync_hours_from_segments(session: OrmSession, _context, _instances) -> None:
    """这次 flush 里动过段的那些记录，合计按段重算。

    合计只此一处写：写段的路径有七八条（签到结算、手动加减、名单导入、本人选
    报酬方式、申诉改判），逐处手写加法迟早漏一处，漏了就是永久对不上账。在一笔
    之内保证 hours 恒等于各段之和（原始值）。
    """
    deleted_records = {r.id for r in session.deleted if isinstance(r, HoursRecord)}
    touched: dict[str, HoursRecord] = {}
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if not isinstance(obj, HoursSegment):
            continue
        rec = _segment_record(session, obj)
        if rec is not None and rec.id not in deleted_records:
            touched[rec.id] = rec
    for rec in touched.values():
        rec.hours = HoursRecord.sum_segments(rec.live_segments())


class HoursRequest(Base):
    """时长的两道审批：锁定与解锁，都由归口部长拍板。

    志愿时长要由校青协过目才算数，所以「锁定」不是管理者点一下就完事：
    申诉期满系统自动提交、或管理者手动提交，等部长通过才真的封账。
    审批期间时长冻结不许改，驳回就解冻、改完重新核准再提交。

    解锁是封账之后还要改的那条路：让部长知道改什么、为什么改，
    而不是给管理者留一个「再解锁一次」的按钮。
    """
    __tablename__ = "hours_requests"

    STATUS_LABELS = {"pending": "待审批", "approved": "已同意", "rejected": "已驳回"}
    KIND_LABELS = {"lock": "锁定", "unlock": "解锁"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(8), default="lock", index=True)
    reason: Mapped[str] = mapped_column(String(512))
    requested_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    reply: Mapped[str] = mapped_column(String(512), default="")
    handled_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 放太久没人处理就催一次（抄送会长团），催过记下时间，只催这一次
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def status_label(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)

    def kind_label(self) -> str:
        return self.KIND_LABELS.get(self.kind, self.kind)


class AttireTask(Base):
    """服装二维码。一场活动两个码：一个领取，一个归还。

    不做「同一个码扫两次」——领取和归还是两个物理动作，发生在活动的两头，
    现场也常常是两张桌子。两个码各贴各的，谁也不会扫错。
    """
    __tablename__ = "attire_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    action: Mapped[str] = mapped_column(String(8))       # take 领取 / return 归还
    token: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    ACTION_LABELS = {"take": "领取", "return": "归还"}

    def action_label(self) -> str:
        return self.ACTION_LABELS.get(self.action, self.action)


class AttireRecord(Base):
    """一个人在一场活动里的服装台账。一人一条，领了记时间，还了再记一次。"""
    __tablename__ = "attire_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    taken_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    taken_method: Mapped[str] = mapped_column(String(12), default="scan")
    returned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    returned_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    returned_method: Mapped[str] = mapped_column(String(12), default="")

    __table_args__ = (
        Index("ux_attire_user_activity", "activity_id", "user_id", unique=True),
    )


class HoursAppeal(Base):
    """时长申诉：志愿者对某条已确认的时长提异议，管理者采纳或驳回。

    自报只走这条兜底路，不做主路径——全员自报会系统性虚高（不是诚信问题，
    是记忆问题），而且管理者要逐条判断，比扫码贵得多。出了岔子的那一条
    （忘记签退、中途被叫走）才值得单独说一次。
    """
    __tablename__ = "hours_appeals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    record_id: Mapped[str] = mapped_column(ForeignKey("hours_records.id"),
                                           index=True)
    activity_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    old_hours: Mapped[float] = mapped_column(Float)      # 提交时的原值，留证
    claimed_hours: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(12), default="pending",
                                        index=True)      # pending/accepted/rejected
    reply: Mapped[str] = mapped_column(String(512), default="")
    final_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    handled_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    STATUS_LABELS = {"pending": "待处理", "accepted": "已采纳", "rejected": "未采纳"}

    def status_label(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)


class HoursClaim(Base):
    """校外自行报名活动的时长补登：本人申报，对外联络部审批。

    不进 HoursRecord：校外活动在平台上没有场次、没有报名、没有签到，塞进
    「一场活动一起结账」那一套（核准 → 确认/申诉 → 提交审核 → 外联确认），
    会让活动的每一条判断都得为它开一个岔路。补登自己一条台账，通过的条目
    单独导出给志愿北京录入（见 exporter.build_claims_workbook）。

    证明材料与活动照片挂在 Attachment 上（owner_type=claim_proof / claim_photo，
    activity_id 留空），跟反馈截图一样不属于任何活动。
    """
    __tablename__ = "hours_claims"

    STATUS_LABELS = {"pending": "待审批", "approved": "已通过",
                     "rejected": "已驳回", "revoked": "已撤销"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(128))          # 活动名称
    # 活动日期。只记哪一天，不记几点到几点：校外活动的起止时刻本人多半记不准，
    # 证明材料上通常也只有日期；同名同天去重、导出都按这一天
    service_date: Mapped[date] = mapped_column(Date)
    hours: Mapped[float] = mapped_column(Float)              # 本人申报的时长
    # 审批核定的时长：外联可能核减，默认等于申报值。计入时长的是它
    approved_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    service_desc: Mapped[str] = mapped_column(String(1000))
    note: Mapped[str] = mapped_column(String(256), default="")   # 备注，选填
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    reply: Mapped[str] = mapped_column(String(512), default="")
    handled_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 放太久没人审批就催一次，催过记下时间，只催这一次
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def status_label(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)

    def counted_hours(self) -> float:
        """计入志愿时长的小时数：通过了才算，按核定值。"""
        if self.status != "approved":
            return 0.0
        return self.approved_hours if self.approved_hours is not None else self.hours


class ViolationRecord(Base):
    """违规记录：活动结束后由管理者在名单里登记的一行。

    只是记录，不产生任何后果——平台不对志愿者做任何限制。青协工作人员在
    志愿者档案里能看到这些记录、他参与过的活动和锁定后退出的次数，仅此而已。
    """
    __tablename__ = "violation_records"

    KINDS = {"late": "迟到早退", "leave": "无故请假", "passive": "消极志愿",
             "other": "其他"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    activity_id: Mapped[str] = mapped_column(String(32), index=True)
    activity_title: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(String(512), default="")   # 情况简述
    operator_id: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def kind_label(self) -> str:
        return self.KINDS.get(self.kind, self.kind)


class AuditLog(Base):
    """操作留痕中心。发起方/青协的增删改全记这里，志愿者可在活动页看到摘要。

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
        # 动作的中文名跟着记录一起发：前端各写一份映射，早晚漏掉某个动作，
        # 那一条就在页面上露出英文
        from .audit import ACTION_LABELS
        changes = [c for c in (self.changes or [])
                   if not str(c.get("field", "")).startswith("_")]
        identity = next((c.get("new", "") for c in (self.changes or [])
                         if c.get("field") == IDENTITY_FIELD), "")
        return {
            "id": self.id, "actor_name": self.actor_name, "action": self.action,
            "action_label": ACTION_LABELS.get(self.action, self.action),
            "actor_identity": identity,
            "target_type": self.target_type, "target_label": self.target_label,
            "summary": self.summary, "changes": changes,
            "created_at": iso(self.created_at), "visible": self.visible,
        }


class Notification(Base):
    """消息通知。真实模式下代表「企业微信模板消息」的送达记录，
    演示模式（Mock）下就是前端「消息中心」的收件箱本身。"""
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    template_key: Mapped[str] = mapped_column(String(32), default="")   # 见 integrations/msgcenter.py 模板库
    # 通道名，固定 "wecom"。它记的是「走的哪个通道」（企业微信＝开放平台的通道 4），
    # 不是「哪个模块发的」，所以不跟发送模块 msgcenter 同名。
    channel: Mapped[str] = mapped_column(String(16), default="wecom")
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    delivered: Mapped[bool] = mapped_column(Boolean, default=True)      # Mock 模式下恒 True
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

    固定两个 key：
      msgcenter / sso
    enabled=false 时走内置 Mock 实现（演示模式），true 时走真实 HTTP 实现。
    """
    __tablename__ = "integration_configs"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Announcement(Base):
    """平台弹窗公告：只有一行（id 固定为 CURRENT），管理员改它、开关它。

    用户每次登录后进入平台时弹一次（前端在登录成功时打一个标记，见
    web/src/store.js 的 setAuth）。已登录的人不会因为公告改了而再弹。
    """
    __tablename__ = "announcements"

    CURRENT = "current"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    title: Mapped[str] = mapped_column(String(60), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ======================= 意见反馈 =======================

class Feedback(Base):
    """用户对**平台本身**的意见反馈（bug、用不顺手的地方）。

    志愿工作上的问题（活动安排、时长、组织）不在这里收，前端把人引到校青协的
    志愿工作反馈平台去——两件事归不同的人管，混在一起两边都处理不好。
    截图挂在 Attachment 上（owner_type="feedback"，owner_id = 本条 id）。
    """
    __tablename__ = "feedbacks"

    STATUS_LABELS = {"open": "待处理", "handled": "已处理"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    reply: Mapped[str] = mapped_column(Text, default="")
    handled_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)

    def status_label(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)
