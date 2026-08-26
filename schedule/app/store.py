"""项目的存取。**这里是数据库的唯一入口。**

之所以要有这么一层而不是让路由直接写 SQL：项目里存着全员的姓名、身份证号、
联系方式，「按 owner 过滤」这件事必须只写一次、写在拿得到的地方。路由里每
写一次 `where(...)` 就多一次漏掉 owner 的机会，而漏掉的后果是拿 UUID 猜别人
的名单。

**跨用户访问也只有一个入口：`get_for()`。** dev 可以读写任何人的项目，
但那条路径必然经过它，也就必然落一条审计。要是路由里再写一个「dev 就放行」
的分支，审计会在那个分支上悄悄缺失——而缺失的审计不会报错。
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import Identity
from app.config import get_settings
from app.core import Project
from app.models import (ScheduleAdminAccess, ScheduleProject,
                        ScheduleProjectInvite, ScheduleProjectMember,
                        ScheduleProjectVersion, default_expires_at)

logger = logging.getLogger(__name__)

# 项目名长度上限，和列定义对齐。
MAX_NAME = 128
# 一个人最多能存多少个项目。不是怕存不下，是防止脚本刷着建。
MAX_PER_USER = 50


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="项目名不能为空")
    if len(name) > MAX_NAME:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="项目名太长")
    return name


def _member_project_ids(db: Session, user_id: uuid.UUID):
    """这个人作为协作者能看到哪些项目。"""
    return select(ScheduleProjectMember.project_id).where(
        ScheduleProjectMember.user_id == user_id
    )


def require_owner(row: ScheduleProject, identity: Identity, what: str) -> None:
    """只有**创建者**能做的事。

    协作者其余权限和创建者一致（能改、能导出、能加人），唯独这一档不行:
    删除是不可逆的,而且会连历史版本一起 CASCADE 掉——历史本来是给误操作兜底的,
    删项目恰恰把那张网也一起撤了。

    dev 放行(他本来就能跨用户读写,且已经落过审计)。

    回 403 不回 404:协作者本来就看得见这个项目,没有「存在性」需要藏,
    这时候回 404 只会让人以为项目没了。
    """
    if row.ruc_user_id == identity.user_id or identity.is_dev:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"只有项目的创建者能{what}。你是协作者，其它都能改，就这一项不行——"
               f"要{what}请找建这个项目的人。",
    )


def list_projects(db: Session, owner: uuid.UUID | None) -> list[ScheduleProject]:
    """owner 为 None 表示**全站**。只有 dev 能这么调，判定在路由层。

    普通用户看到的是「自己建的 + 别人拉自己进去的」。列表里不含 `data`，
    所以它本身不暴露名单内容；真正会读到身份证号的是 `get_for()`。
    """
    stmt = select(ScheduleProject).order_by(ScheduleProject.updated_at.desc())
    if owner is not None:
        stmt = stmt.where(
            or_(
                ScheduleProject.ruc_user_id == owner,
                ScheduleProject.id.in_(_member_project_ids(db, owner)),
            )
        )
    return list(db.scalars(stmt))


def get_owned(db: Session, owner: uuid.UUID, project_id: uuid.UUID) -> ScheduleProject:
    """按 (id, 拥有者或协作者) 取。**故意不区分「不存在」和「你看不到」**——

    两者都回 404。区分开的话，拿 UUID 逐个试就能问出「这个项目存在但我看不到」，
    等于把别人有哪些项目泄露出去了。

    协作者和创建者权限基本一致，所以这里不分档：能取到就能改。分级只会带来
    「他改不了这一格，得去找建项目的人」，而这个工具的场景就是一个活动的
    几个负责人一起排班。

    **唯一的例外是删除项目**，那一档收给创建者——见 `require_owner`。
    它不在这里判：这个函数管的是「看不看得见」，删除管的是「能不能做」，
    两件事混在一起的话，协作者读项目也会被拦。
    """
    row = db.scalar(
        select(ScheduleProject).where(
            ScheduleProject.id == project_id,
            or_(
                ScheduleProject.ruc_user_id == owner,
                ScheduleProject.id.in_(_member_project_ids(db, owner)),
            ),
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return row


def get_for(db: Session, identity: Identity, project_id: uuid.UUID, action: str) -> ScheduleProject:
    """按身份取项目。**所有路由都走这个函数，不要再直接调 get_owned。**

    普通用户等价于 get_owned；dev 可以拿到任何人的，但每一次都会落一条
    审计（见 ScheduleAdminAccess）。`action` 是给审计看的动词，别偷懒传空。
    """
    if not identity.is_dev:
        row = get_owned(db, identity.user_id, project_id)
        _refresh_nickname(db, row, identity)
        return row

    row = db.scalar(select(ScheduleProject).where(ScheduleProject.id == project_id))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    if row.ruc_user_id == identity.user_id:
        _refresh_nickname(db, row, identity)
    else:
        _audit(db, identity, row, action)
    return row


def _refresh_nickname(db: Session, row: ScheduleProject, identity: Identity) -> None:
    """顺手把昵称快照刷新成最新的。

    只在**拥有者本人**碰这个项目时刷——dev 路过时刷会把快照写成 dev 自己的名字。
    改名之后要等本人下次打开才更新，这个滞后可以接受：它只用于 dev 那份列表。
    """
    if identity.nickname and row.owner_nickname != identity.nickname:
        row.owner_nickname = identity.nickname
        db.commit()


def _audit(db: Session, identity: Identity, row: ScheduleProject, action: str) -> None:
    """记一条跨用户访问。

    **和业务写在同一个事务里**：审计成功而业务回滚，会留下一条没发生过的记录；
    业务成功而审计回滚，会留下一次没有痕迹的读取。后者才是真问题，所以宁可
    两者同生共死，也不单独 commit。

    读操作（GET）走到这里时调用方通常不再 commit，所以这里补一次 flush + commit；
    写操作会在后面的 _touch 里一起提交，重复 commit 是幂等的。
    """
    db.add(
        ScheduleAdminAccess(
            actor_id=identity.user_id,
            actor_nickname=identity.nickname,
            project_id=row.id,
            project_name=row.name,
            owner_id=row.ruc_user_id,
            action=action,
        )
    )
    db.commit()
    # 日志是给「现在正在发生什么」看的，审计表是给「事后查」看的，两个都要。
    logger.warning(
        "dev 跨用户访问排班项目：actor=%s(%s) action=%s project=%s owner=%s",
        identity.nickname,
        identity.user_id,
        action,
        row.id,
        row.ruc_user_id,
    )


def create(db: Session, owner: uuid.UUID, name: str, data: dict | None = None, nickname: str = "") -> ScheduleProject:
    count = db.scalar(
        select(func.count())
        .select_from(ScheduleProject)
        .where(ScheduleProject.ruc_user_id == owner)
    )
    if count is not None and count >= MAX_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"最多只能有 {MAX_PER_USER} 个项目，请先删掉用不上的",
        )
    row = ScheduleProject(
        ruc_user_id=owner,
        owner_nickname=nickname,
        name=_clean_name(name),
        data=data if data is not None else Project().to_dict(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def rename(db: Session, row: ScheduleProject, name: str) -> ScheduleProject:
    row.name = _clean_name(name)
    return _touch(db, row)


def renew(db: Session, row: ScheduleProject) -> ScheduleProject:
    """续期：到期日推到今天往后 retention_days 天。"""
    row.expires_at = default_expires_at()
    db.commit()
    db.refresh(row)
    return row


def save_data(
    db: Session,
    row: ScheduleProject,
    data: dict,
    expected_version: int | None,
    identity: Identity | None = None,
    action: str = "",
) -> ScheduleProject:
    """整份状态写回，顺手记一版历史。

    expected_version 不为 None 时做乐观并发检查——两个标签页开着同一个项目，
    后写的那个不该把前一个的改动无声盖掉。

    `action` 空 = 手动微调（会按时间窗口合并），非空 = 重要节点的名字。
    **由调用方给**：只有路由知道这次 PUT 到底是「应用排班」还是「勾了个锁」，
    服务端拿新旧两份 data 去反推既脆弱又猜不准。
    """
    if expected_version is not None and expected_version != row.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="这个项目在别处被改过了，请刷新后重试（避免覆盖掉别人的改动）",
        )
    # 每一次写都要占住编辑权。**放在版本检查之后**：版本对不上是更具体的错，
    # 先说那句话对人更有用；而且那种情况下本来就不该改任何状态。
    if identity is not None:
        claim_lock(db, row, identity)
    row.data = data
    updated = _touch(db, row)
    if identity is not None:
        record_version(db, updated, identity, action)
    return updated


# ======================= 编辑占用 =======================
#
# **同一时刻只有一个人能改。** 整份状态 PUT + 乐观并发这套模型单人用很省心，
# 多人用就成了主要痛点：一个人改岗位条件、另一个人勾冲突矩阵，两件毫不相干的事
# 也会互相 409，而且是写完半天才被打回。与其让人白干，不如一开始就说清
# 「现在轮不到你」。
#
# 靠时间戳自动过期，不需要显式释放：浏览器直接关掉、网断了、人走了，这些情况下
# 没人会来解锁，而一把解不开的锁比没有锁更糟。

LOCK_TTL = timedelta(minutes=5)


def lock_holder(row: ScheduleProject) -> tuple[uuid.UUID, str] | None:
    """现在谁占着。没人占、或者占用已经过期，返回 None。"""
    if row.editing_by is None or row.editing_at is None:
        return None
    if datetime.now(UTC) - row.editing_at > LOCK_TTL:
        return None
    return row.editing_by, row.editing_name


def claim_lock(db: Session, row: ScheduleProject, identity: Identity, force: bool = False) -> None:
    """占住编辑权（或续期）。别人正占着且没过期就抛 409。

    `force` 是给「他挂着页面走了」准备的：占用五分钟才过期，而人可能只是
    开着标签页去吃饭了。没有这个口子的话，另一个人只能干等——那种等待
    没有任何用户价值，只会让人去关掉对方的浏览器。**接管会记在历史里**
    （下一次写入的 actor 就是接管的人），所以不是无痕的。
    """
    held = lock_holder(row)
    if held is not None and held[0] != identity.user_id and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"「{held[1] or '另一个人'}」正在编辑这个项目，"
                   f"请等他改完，或者在协作页面点「接管编辑」。",
        )
    row.editing_by = identity.user_id
    row.editing_name = identity.nickname
    row.editing_at = datetime.now(UTC)
    db.commit()


def release_lock(db: Session, row: ScheduleProject, identity: Identity) -> None:
    """主动让出。只有占着的人能放——不然点一下就能把别人踢掉，锁等于没有。"""
    if row.editing_by == identity.user_id:
        row.editing_by = None
        row.editing_name = ""
        row.editing_at = None
        db.commit()


# ======================= 协作者与邀请 =======================

# 邀请链接的有效期。短一点：谁拿到链接谁就能进，而链接会被转发。
INVITE_TTL = timedelta(days=7)
MAX_MEMBERS = 20


def list_members(db: Session, project_id: uuid.UUID) -> list[ScheduleProjectMember]:
    return list(
        db.scalars(
            select(ScheduleProjectMember)
            .where(ScheduleProjectMember.project_id == project_id)
            .order_by(ScheduleProjectMember.joined_at)
        )
    )


def remove_member(db: Session, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    db.execute(
        delete(ScheduleProjectMember).where(
            ScheduleProjectMember.project_id == project_id,
            ScheduleProjectMember.user_id == user_id,
        )
    )
    db.commit()


def create_invite(db: Session, row: ScheduleProject, identity: Identity) -> ScheduleProjectInvite:
    invite = ScheduleProjectInvite(
        project_id=row.id,
        # token_urlsafe(32) ≈ 256 位熵。链接就是凭据，猜得到就等于名单泄露。
        token=secrets.token_urlsafe(32),
        created_by=identity.user_id,
        created_nickname=identity.nickname,
        expires_at=datetime.now(UTC) + INVITE_TTL,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


def list_invites(db: Session, project_id: uuid.UUID) -> list[ScheduleProjectInvite]:
    return list(
        db.scalars(
            select(ScheduleProjectInvite)
            .where(ScheduleProjectInvite.project_id == project_id)
            .order_by(ScheduleProjectInvite.created_at.desc())
        )
    )


def revoke_invite(db: Session, project_id: uuid.UUID, invite_id: uuid.UUID) -> None:
    invite = db.scalar(
        select(ScheduleProjectInvite).where(
            ScheduleProjectInvite.id == invite_id,
            ScheduleProjectInvite.project_id == project_id,
        )
    )
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="这条邀请不存在")
    invite.revoked_at = datetime.now(UTC)
    db.commit()


def accept_invite(db: Session, token: str, identity: Identity) -> ScheduleProject:
    """拿链接加入。返回项目。

    失效的链接**一律回同一句话**，不区分「不存在 / 过期了 / 撤销了」——
    区分开的话，拿 token 逐个试就能问出「这个 token 存在过」。
    """
    invite = db.scalar(select(ScheduleProjectInvite).where(ScheduleProjectInvite.token == token))
    bad = HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="这个邀请链接无效或已过期，找建项目的人重新发一个。",
    )
    if invite is None or invite.revoked_at is not None:
        raise bad
    if invite.expires_at < datetime.now(UTC):
        raise bad

    row = db.scalar(select(ScheduleProject).where(ScheduleProject.id == invite.project_id))
    if row is None:
        raise bad
    if row.ruc_user_id == identity.user_id:
        return row      # 自己建的项目，点自己的链接：直接放行，不用加成员

    exists = db.scalar(
        select(ScheduleProjectMember).where(
            ScheduleProjectMember.project_id == row.id,
            ScheduleProjectMember.user_id == identity.user_id,
        )
    )
    if exists is not None:
        return row

    if len(list_members(db, row.id)) >= MAX_MEMBERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"这个项目的协作者已经有 {MAX_MEMBERS} 个了",
        )

    db.add(
        ScheduleProjectMember(
            project_id=row.id, user_id=identity.user_id, nickname=identity.nickname
        )
    )
    invite.used_count += 1
    try:
        db.commit()
    except IntegrityError:
        # 两个标签页同时点同一条链接。唯一约束拦下了，这里当成「已经在里面」。
        db.rollback()
    return row


# ======================= 历史版本 =======================

# 每个项目最多留几版。整份 data 有近 1MB，50 版是几十 MB 的量级，卡死。
MAX_VERSIONS = 50
# 手动微调的合并窗口。窗口从这一版第一次编辑起算。
MANUAL_WINDOW = timedelta(minutes=3)


def record_version(db: Session, row: ScheduleProject, identity: Identity, action: str) -> None:
    """记一版历史。重要节点单独存，手动微调按窗口合并。

    合并的锚点是 `created_at`（窗口起点），**不是** `saved_at`：拿最后一次编辑
    当锚点的话，窗口会跟着一直往后滑，一个人连着改一小时也只会留下一版，
    而那一版里什么都恢复不到。
    """
    now = datetime.now(UTC)
    action = (action or "").strip()[:32]

    if not action:
        latest = db.scalar(
            select(ScheduleProjectVersion)
            .where(ScheduleProjectVersion.project_id == row.id)
            .order_by(ScheduleProjectVersion.created_at.desc())
            .limit(1)
        )
        mergeable = (
            latest is not None
            and not latest.action
            and latest.actor_id == identity.user_id
            and now - latest.created_at < MANUAL_WINDOW
        )
        if mergeable:
            latest.data = row.data
            latest.version = row.version
            latest.saved_at = now
            db.commit()
            return

    db.add(
        ScheduleProjectVersion(
            project_id=row.id,
            version=row.version,
            data=row.data,
            action=action,
            actor_id=identity.user_id,
            actor_nickname=identity.nickname,
            created_at=now,
            saved_at=now,
        )
    )
    db.commit()
    _trim_versions(db, row.id)


def _trim_versions(db: Session, project_id: uuid.UUID) -> None:
    keep = db.scalars(
        select(ScheduleProjectVersion.id)
        .where(ScheduleProjectVersion.project_id == project_id)
        .order_by(ScheduleProjectVersion.created_at.desc())
        .limit(MAX_VERSIONS)
    ).all()
    if len(keep) < MAX_VERSIONS:
        return
    db.execute(
        delete(ScheduleProjectVersion).where(
            ScheduleProjectVersion.project_id == project_id,
            ScheduleProjectVersion.id.notin_(keep),
        )
    )
    db.commit()


def list_versions(db: Session, project_id: uuid.UUID) -> list[ScheduleProjectVersion]:
    """列表**不含 data**——那是几十 MB 的东西，列一次表没必要全带出来。"""
    return list(
        db.scalars(
            select(ScheduleProjectVersion)
            .where(ScheduleProjectVersion.project_id == project_id)
            .order_by(ScheduleProjectVersion.created_at.desc())
        )
    )


def get_version(db: Session, project_id: uuid.UUID, version_id: uuid.UUID) -> ScheduleProjectVersion:
    """按 (项目, 版本) 取。**必须带上 project_id** ——只按 version_id 查的话，
    拿别人项目的版本 id 就能读到内容，而项目的 owner 过滤就白做了。"""
    found = db.scalar(
        select(ScheduleProjectVersion).where(
            ScheduleProjectVersion.id == version_id,
            ScheduleProjectVersion.project_id == project_id,
        )
    )
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="这一版不存在")
    return found


def restore_version(
    db: Session, row: ScheduleProject, snapshot: ScheduleProjectVersion, identity: Identity
) -> ScheduleProject:
    """恢复到某一版。

    **是「新写一版，内容等于旧版」，不是把版本号回退。** 版本号是乐观并发的依据，
    让它倒退等于把并发保护废掉：另一个标签页拿着更大的号，之后的写入会莫名其妙
    地通过或失败。所以恢复本身也是一次前进，而且它自己也进历史——
    恢复错了还能再恢复回来。
    """
    row.data = snapshot.data
    updated = _touch(db, row)
    record_version(db, updated, identity, f"恢复到 {snapshot.created_at:%m-%d %H:%M}")
    return updated


def _touch(db: Session, row: ScheduleProject) -> ScheduleProject:
    row.version += 1
    row.updated_at = datetime.now(UTC)
    # 动过就顺手续期：还在用的项目不该因为「建了 180 天」而消失。
    row.expires_at = default_expires_at()
    db.commit()
    db.refresh(row)
    return row


def remove(db: Session, row: ScheduleProject) -> None:
    db.delete(row)
    db.commit()


def purge_expired(db: Session) -> int:
    """删掉过期项目。返回删了几个。

    真删而不是软删：留着一份「已删除但还在库里」的联系方式名单，等于没删。
    """
    result = db.execute(delete(ScheduleProject).where(ScheduleProject.expires_at < datetime.now(UTC)))
    db.commit()
    return result.rowcount or 0


def expiring_soon(row: ScheduleProject) -> bool:
    days = get_settings().expiring_soon_days
    return row.expires_at < datetime.now(UTC) + timedelta(days=days)


def load_core(row: ScheduleProject) -> Project:
    """JSONB → core.Project。坏数据在这里变成 500 之外的东西。"""
    try:
        return Project.from_dict(row.data or {})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"项目数据读取失败：{exc}",
        ) from exc


def check_size(project: Project) -> None:
    """规模上限。超了当场拒，而不是让人等满 30 秒才知道表传错了。"""
    settings = get_settings()
    if len(project.positions) > settings.max_positions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"岗位数超过上限 {settings.max_positions}，请拆分成多个项目",
        )
    if len(project.volunteers) > settings.max_volunteers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"人数超过上限 {settings.max_volunteers}，请拆分成多个项目",
        )
    if len(project.signups) > settings.max_signups:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"报名条目超过上限 {settings.max_signups}，请拆分成多个项目",
        )
