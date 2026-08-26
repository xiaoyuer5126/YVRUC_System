from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import DB_SCHEMA, get_settings
from app.db import Base


def _now() -> datetime:
    return datetime.now(UTC)


def default_expires_at() -> datetime:
    return _now() + timedelta(days=get_settings().retention_days)


class ScheduleProject(Base):
    """一个排班项目 = 桌面版的那个 .json 文件。

    **整份状态存在一个 JSONB 字段里**，而不是拆成岗位/志愿者/报名/排班四张表。
    理由是 `core.Project` 本来就有一套 `to_dict()/from_dict()`，序列化格式一个字
    没改就能直接用；而这个工具从来不会「只查某个志愿者」——求解、导出、
    候补队列，每一件事都要把整份状态读进内存。拆表只会换来一堆 join 和
    一份必须和 core.py 同步演进的映射代码。

    ⚠️ **data 里有姓名、身份证号、联系方式、微信。** 所以：
      · 每一次读写都必须过 store.py（那里是唯一的入口），普通用户一律按 owner 过滤；
      · **只有 dev 能跨用户读写**，而且每一次都会往 admin_access_log 落一行，
        见 ScheduleAdminAccess；admin 没有这个能力，理由见 auth.COMMUNITY_DEV_ROLE；
      · 到期自动删除，见 expires_at。
    """

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 项目归谁。**所有查询都按它过滤**，不存在跨用户读取的路径。
    ruc_user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    # core.Project.to_dict() 的原样。
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # 乐观并发：每次写 +1。前端「预览重排 → 确认应用」两步之间要靠它确认
    # 项目没被另一个标签页改过——排班是确定性的，只要版本没变，
    # 预览看到的和应用下去的就是同一份。
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    # 项目归属人的昵称快照。这个服务只认 user_id，不存社区的用户表；
    # dev 看全站列表时若只有一串 UUID，「监控运行状况」就无从谈起。
    # 拥有者每次动这个项目都会刷新它，所以最多滞后到他下一次操作。
    owner_nickname: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # 到期就删。项目里躺着全员的联系方式，活动结束之后没人会想起来清理，
    # 所以让它自己消失；快到期时前端会提示，点一下就能续。
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=default_expires_at, index=True
    )

    # —— 编辑占用。**同一时刻只有一个人能改。** ——
    #
    # 整份状态 PUT + 乐观并发这套模型，单人用很省心，多人用就成了主要痛点:
    # 一个人在改岗位条件、另一个人在勾冲突矩阵,两件毫不相干的事也会互相 409,
    # 而且是写完半天才被打回。与其让人白干,不如一开始就说清「现在轮不到你」。
    #
    # 靠时间戳自动过期(见 store.LOCK_TTL),不需要显式释放——浏览器直接关掉、
    # 网断了、人走了,这些情况下没人会来解锁,而一把解不开的锁比没有锁更糟。
    editing_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    editing_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    editing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScheduleProjectMember(Base):
    """项目的协作者。**权限和创建者一致**（含删除项目）。

    这是明确的产品选择，不是省事：这个工具的使用场景是一个活动的几个负责人
    一起排班，分级权限只会带来「他改不了这一格，得去找建项目的人」。代价是
    任何协作者都能删掉整个项目——所以删除有二次确认，而且历史版本兜底。

    ⚠️ 加人 = 把全员的身份证号、联系方式交给对方。所以只能靠邀请链接加，
    没有「搜用户名直接加」——那样手滑点错一个人就把名单给出去了。
    """

    __tablename__ = "project_members"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        # 同一个人不该在同一个项目里出现两次。靠链接加人时，两个标签页同时点
        # 就会撞上——交给数据库拦，比在应用层查一遍再插靠得住。
        UniqueConstraint("project_id", "user_id", name="uq_project_member"),
        {"schema": DB_SCHEMA},
    )


class ScheduleProjectInvite(Base):
    """邀请链接。

    **只能靠链接加人，没有「按昵称搜索直接加」。** 两条理由：一是这个服务
    压根不认识社区里的其他用户（`auth.py` 只做「拿 cookie 换 user_id」，
    要按名字找人得跨服务要一个用户目录接口）；二是更要紧的——加人等于把全员的
    身份证号交出去，搜索列表里手滑点错一个人就没法收回，而链接是你主动发出去的。

    token 一次生成、多次可用，直到过期或撤销。谁拿到链接谁就能进，所以
    有效期短、可随时撤销、且**列表里能看见每条链接是谁建的**。
    """

    __tablename__ = "project_invites"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    created_nickname: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = ({"schema": DB_SCHEMA},)


class ScheduleProjectVersion(Base):
    """项目的历史快照。**整份 data 原样存一份。**

    存得起是因为 data 本来就是一整块 JSONB——存历史几乎就是复制一行。
    但也正因为整份存，条数必须卡死:一份大项目的 data 有近 1MB,
    50 版就是几十 MB(JSONB 走 TOAST 压缩之后小一些,但仍然比项目本身大一个量级)。

    ## 存什么、不存什么

    **不是每次 PUT 都存一版。** 勾个锁、调个岗都会产生一次 PUT,全存下来的话
    50 版可能只覆盖十分钟的操作,根本回不到「昨天那一版」——那这个功能就白做了。
    所以分两类:

    - **重要节点**(`action` 非空):应用排班、导入表格、清空、改规则。每次都单独存。
    - **手动微调**(`action` 为空):按 `MANUAL_WINDOW` 合并成一版。窗口从这一版
      第一次编辑起算,期间的连续改动都往同一行上盖,只更新 `data` 和 `saved_at`。
      锚点用 `created_at`,不是 `saved_at`——用后者的话窗口会跟着最后一次编辑
      一直往后滑,一个人连着改一小时也只会产生一版。

    ⚠️ 里面同样有姓名、身份证号、联系方式。所以外键 `ON DELETE CASCADE`:
    项目删了(手动删或到期自动删),历史必须跟着一起没,否则「删是真删」这句话
    就不成立了。
    """

    __tablename__ = "project_versions"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(f"{DB_SCHEMA}.projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 这一版对应项目的哪个 version。恢复时不回退它,见 store.restore_version。
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # 空 = 手动微调(可合并);非空 = 重要节点的名字,如「应用排班」。
    action: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    actor_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    # 昵称快照:这个服务不存社区的用户表,只留 UUID 的话事后看不出是谁。
    actor_nickname: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # 窗口起点。合并时**不动它**——它是「这一版从什么时候开始攒的」。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, index=True
    )
    # 窗口里最后一次落盘的时间。重要节点上它等于 created_at。
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class ScheduleAdminAccess(Base):
    """dev 跨用户碰别人项目的流水。**不提供删除接口。**

    这张表是「dev 能看全部项目」这件事站得住的全部理由。名单一直在库里，
    dev 技术上随时读得到，真正的区别不在「能不能查」而在「查了有没有痕迹」——
    和社区那边的 anonymity_reveals 是同一个道理。没有它，所谓的
    「只有你自己看得到」就变成了「对同学不可见、对运维透明」，而用户
    并不知道这一点。

    只记跨用户的操作：dev 动自己的项目和普通用户没区别，记了只是噪声。
    """

    __tablename__ = "admin_access_log"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    # 谁看的。昵称一起存：user_id 事后还要去社区查一次才知道是谁。
    actor_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    actor_nickname: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # 看的是谁的哪个项目。**项目删掉之后这行要留着**，所以不建外键。
    project_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    project_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    # read / write / delete / export / solve —— 由路由传进来，见 store.get_for。
    action: Mapped[str] = mapped_column(String(16), nullable=False)
