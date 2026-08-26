"""项目的增删改查、导入导出、排班。

接口划分的一条原则：**服务端只做客户端做不了的事**——解析表格、跑求解器、
生成 xlsx。锁定/调岗/勾冲突矩阵这些纯粹是改状态，客户端改完整份存回来
（`PUT /projects/{id}`），和桌面版「整个项目就是一个文件」是同一个模型。
这样不必为每一个手工动作开一个接口，也就不会有一堆各自校验一遍的入口。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.auth import Identity, require_login
from app.config import get_settings
from app import rules as rulecfg
from app.core import (LEADER, Project, SolveTimeout, diff_pairs,
                      position_stats)
from app.dataio import (apply_sample_conditions, export_xlsx, import_positions,
                        import_signups, sample_xlsx)
from app.db import get_db
from app.models import ScheduleProject
from app.schemas import (CreateProject, PreviewRules, ProjectDetail,
                         ProjectSummary, ProjectVersion, RenameProject,
                         SaveProject, SolveRequest, SolveResult)
from app.solver import run_solve
from app.views import derive
from app import solve_guard, store

router = APIRouter(tags=["projects"])


def _summary_fields(row: ScheduleProject, viewer: Identity) -> dict:
    data = row.data or {}
    mine = row.ruc_user_id == viewer.user_id
    return {
        "id": row.id,
        "name": row.name,
        "version": row.version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "expires_at": row.expires_at,
        "expiring_soon": store.expiring_soon(row),
        "positions": len(data.get("positions") or []),
        "volunteers": len(data.get("volunteers") or []),
        "assignments": len(data.get("assignments") or []),
        "mine": mine,
        # 别人的项目才带归属人。自己的行上写自己的名字没有信息量。
        "owner_nickname": None if mine else (row.owner_nickname or "（未记录）"),
        # 三种身份要分开：**协作者和「dev 在看别人的项目」不是一回事**。
        # 只有一个 mine 布尔的话，界面会对协作者说「你以开发者身份打开」，那是句假话。
        #
        # 不用查成员表：能走到这里说明 get_for 放行了，而放行只有三条路——
        # 拥有者、协作者、dev。不是拥有者又不是 dev，那就只能是协作者。
        # （既是 dev 又真的是协作者时会显示成 dev。那是更谨慎的那句话，
        # 而且为这个边角去查一次库不值。）
        "role": "owner" if mine else ("dev" if viewer.is_dev else "member"),
        # 删除只有创建者能做（dev 也行，他本来就能跨用户读写且已落审计）。
        # 前端据此决定要不要画那个按钮——服务端照样会再拦一次。
        "can_delete": mine or viewer.is_dev,
    }


def _summary(row: ScheduleProject, viewer: Identity) -> ProjectSummary:
    return ProjectSummary(**_summary_fields(row, viewer))


def _detail(row: ScheduleProject, viewer: Identity) -> ProjectDetail:
    """整份项目。

    ⚠️ **下发的是 `project.to_dict()`，不是 `row.data`。** 数据库里那份原始 JSONB
    的形状不能直接给前端，两条理由都是真出过事的：

    1. **缺字段。** `caps` 是后加的，在那之前建的项目 JSON 里根本没有这个键，
       前端 `draft.caps.push(...)` 直接抛 TypeError，请求都发不出去。以后每加
       一个 ProjectData 字段都会复现，而老项目不会自己长出新键。
    2. **键顺序被重排。** PostgreSQL 的 jsonb **不保留对象键顺序**，它按
       「先长度、再字节」重排后存储。于是 `data.rules` 回来是
       `order/preset/toggles/candidate`，而服务端 `rulecfg.normalize()` 现算的是
       声明顺序 `preset/order/candidate/toggles`——规则页拿这两份做
       `JSON.stringify` 全等比较，永远不等，「保存这套规则」那个按钮从上线起
       就没能点亮过。（SQLite 按原文存，所以本地测不出来，只在 Postgres 上犯。）

    `project` 上面已经算出来了，用它序列化一次就等于「读也走归一化」，
    前端从此永远拿到完整且稳定的形状。
    """
    project = store.load_core(row)
    return ProjectDetail(**_summary_fields(row, viewer),
                         data=project.to_dict(), views=derive(project))


def _owned(project_id: uuid.UUID, identity: Identity, db: Session, action: str) -> ScheduleProject:
    """取项目。`action` 是给审计看的动词——dev 碰别人的项目时会落一行。

    **参数刻意做成必填**：给它一个默认值，下一个新增的路由就会忘了传，
    然后那条路径上的审计写的是「read」而实际改了数据。
    """
    return store.get_for(db, identity, project_id, action)


# --------------------------------------------------------------------- 项目

@router.get("/me")
async def me(identity: Identity = Depends(require_login)) -> dict:
    """给前端一句「你是谁」。这个工具登录即可用，唯一的权限位是 can_see_all。"""
    settings = get_settings()
    return {
        "user_id": str(identity.user_id),
        "nickname": identity.nickname,
        # 能不能看别人的项目。**只有 dev**，理由见 auth.COMMUNITY_DEV_ROLE：
        # 项目里是全员的身份证号，admin 的内容治理职责用不到它。
        "can_see_all": identity.is_dev,
        "retention_days": settings.retention_days,
        # 最坏情况一次排班要这么久（主段 + 降级段 + 保底重试）。前端拿它提示，
        # 免得等到一半以为页面挂了。
        "solve_max_seconds": settings.solve_max_seconds,
        # 规则目录。**规则的名字和预设内容由服务端给**，前端照着画——
        # 两边各维护一份的话，界面上写着「已启用」而求解器根本不认这个键。
        "rule_catalog": {
            "order": rulecfg.ORDER_RULES,
            "candidate": rulecfg.CAND_RULES,
            "toggles": rulecfg.TOGGLES,
            "locked_order_rule": rulecfg.LOCKED_ORDER_RULE,
            "presets": rulecfg.PRESETS,
            "default_preset": rulecfg.DEFAULT_PRESET,
            # 固定规则：不给开关，但要给看。界面上置灰只读。
            "fixed": [
                {"group": g["group"], "note": g.get("note", ""),
                 "items": [{"label": label, "why": why} for label, why in g["items"]]}
                for g in rulecfg.FIXED_RULES
            ],
        },
    }


@router.get("/projects")
async def list_projects(
    all: bool = Query(default=False, description="dev 看全站"),
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    """项目列表。

    `all=1` 只对 dev 生效，别人传了照常只看自己的——**不报错**。这不是权限
    边界上的失败（没有任何东西被越权拿到），报 403 只会让手滑改了 URL 的人
    以为自己踩到了什么。同 bv2008 的历史列表。
    """
    everyone = all and identity.is_dev
    rows = store.list_projects(db, None if everyone else identity.user_id)
    return {
        "items": [_summary(r, identity) for r in rows],
        # 前端据此决定要不要画那个「全部使用者」开关。
        "can_see_all": identity.is_dev,
    }


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: CreateProject,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> ProjectDetail:
    data = None
    if payload.with_sample:
        project = Project()
        import_positions(project, sample_xlsx(), "示例.xlsx")
        # 示例文件第一张 sheet 是岗位表，报名表在第二张——两张分别导入。
        import_signups(project, _sample_signups_sheet(), "示例报名.xlsx")
        # 条件不从表格解析，示例里的条件得单独补上，否则新用户看不到它长什么样。
        apply_sample_conditions(project)
        data = project.to_dict()
    row = store.create(db, identity.user_id, payload.name, data, identity.nickname)
    return _detail(row, identity)


def _sample_signups_sheet() -> bytes:
    """示例报名表单独成一份字节（导入器只读第一张 sheet）。"""
    from openpyxl import Workbook

    from app.dataio import SAMPLE_SIGNUPS

    wb = Workbook()
    ws = wb.active
    for row in SAMPLE_SIGNUPS:
        ws.append(row)
    import io

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@router.get("/projects/{project_id}")
async def read_project(
    project_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> ProjectDetail:
    return _detail(_owned(project_id, identity, db, "read"), identity)


@router.put("/projects/{project_id}")
async def save_project(
    project_id: uuid.UUID,
    payload: SaveProject,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> ProjectDetail:
    """整份状态存回。

    客户端能改的东西（锁定、调岗、勾冲突矩阵、增删岗位/报名）都走这一条。
    这里**要重新解析一遍**：不是为了防谁，是防一份结构坏掉的 JSON 存进去之后
    再也打不开——那时人的名单还在里面。
    """
    row = _owned(project_id, identity, db, "write")
    project = _parse(payload.data)
    store.check_size(project)
    # 存归一化之后的结果而不是原样：多余的字段进不来，格式也就不会慢慢漂。
    updated = store.save_data(db, row, project.to_dict(), payload.version,
                              identity=identity, action=payload.action)
    return _detail(updated, identity)


def _parse(data: dict) -> Project:
    try:
        return Project.from_dict(data or {})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"项目数据格式不对：{exc}",
        ) from exc


@router.patch("/projects/{project_id}")
async def rename_project(
    project_id: uuid.UUID,
    payload: RenameProject,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> ProjectSummary:
    return _summary(store.rename(db, _owned(project_id, identity, db, "rename"), payload.name), identity)


@router.post("/projects/{project_id}/renew")
async def renew_project(
    project_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> ProjectSummary:
    """续期。项目里有全员的联系方式，所以到期会真删——这是那个「再放一阵」的按钮。"""
    return _summary(store.renew(db, _owned(project_id, identity, db, "renew")), identity)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> Response:
    row = _owned(project_id, identity, db, "delete")
    # **只有创建者能删。** 协作者其余权限和创建者一致，唯独这一档不行——
    # 删除不可逆，而且会把历史版本一起 CASCADE 掉，等于把误操作的兜底也撤了。
    store.require_owner(row, identity, "删除这个项目")
    store.remove(db, row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- 导入

async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件是空的")
    limit = get_settings().max_upload_bytes
    if len(data) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件超过 {limit // 1024 // 1024}MB",
        )
    return data


@router.post("/projects/{project_id}/import/positions")
async def upload_positions(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    row = _owned(project_id, identity, db, "import")
    project = store.load_core(row)
    data = await _read_upload(file)
    try:
        warnings = import_positions(project, data, file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    store.check_size(project)
    updated = store.save_data(db, row, project.to_dict(), None,
                              identity=identity, action="导入岗位表")
    return {"warnings": warnings, "project": _detail(updated, identity)}


@router.post("/projects/{project_id}/import/signups")
async def upload_signups(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    row = _owned(project_id, identity, db, "import")
    project = store.load_core(row)
    data = await _read_upload(file)
    try:
        added, warnings = import_signups(project, data, file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    store.check_size(project)
    updated = store.save_data(db, row, project.to_dict(), None,
                              identity=identity, action="导入报名表")
    return {"added": added, "warnings": warnings, "project": _detail(updated, identity)}


# --------------------------------------------------------------------- 排班

@router.post("/projects/{project_id}/solve")
async def solve_project(
    project_id: uuid.UUID,
    payload: SolveRequest,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> SolveResult:
    """重新排班。

    两步走，和桌面版一样：先 `apply=false` 拿变更预览，人确认了再 `apply=true`。
    两次算出来的是同一份——求解器是确定性的，没有任何随机——所以第二步带上
    第一步的版本号就够了，不必把候选结果在服务端存一份。
    """
    row = _owned(project_id, identity, db, "solve")
    if payload.apply and payload.version is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="应用排班要带上版本号")
    if payload.apply and payload.version != row.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="项目在预览之后被改过了，请重新预览确认",
        )

    project = store.load_core(row)
    store.check_size(project)
    before = project.current_pairs()

    # 一个人同时只能算一次。前端会把按钮禁掉，但那只挡得住同一个页面上的连点。
    await solve_guard.acquire(identity.user_id)
    try:
        # 求解是纯 CPU 的同步代码，扔线程里跑，别把事件循环占住——
        # 一分多钟里整个服务不响应，别人连项目列表都打不开。
        outcome = await _run(project, before, identity.user_id)
    except SolveTimeout as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{exc}。多半是岗位之间可以互相替代的组合太多；"
                "可以试着减少「一个人报名的岗位数」，或把活动拆成几个项目分开排。"
            ),
        ) from exc

    project.assignments = outcome.assignments
    rows, counts = diff_pairs(before, project.current_pairs())

    def _name(vk: str) -> str:
        vol = project.volunteers.get(vk)
        return vol.name if vol else vk

    diff = [{"type": r["type"], "name": _name(r["vol_key"]), "from": r["from"], "to": r["to"]}
            for r in rows]

    version = row.version
    detail = None
    if payload.apply:
        # 应用时才记快照:变更表比的是「上一次应用」到现在，预览不该动它。
        project.prev_pairs = before
        updated = store.save_data(db, row, project.to_dict(), payload.version,
                                  identity=identity, action="应用排班")
        version = updated.version
        # 顺手把新状态带回去。前端应用完本来要再 GET 一次整份项目，
        # 那是一趟纯浪费的往返——这里已经算好了。
        detail = _detail(updated, identity)

    return SolveResult(
        version=version,
        diff=diff,
        summary={k: int(v) for k, v in counts.items()},
        degraded=outcome.degraded,
        seconds=round(outcome.seconds, 2),
        rescued=outcome.rescued,
        rescue_timed_out=outcome.rescue_timed_out,
        project=detail,
    )


async def _run(project: Project, before, user_id):
    """把求解丢进线程，并且**在那个线程里**释放并发标记。

    不能在请求侧的 finally 里释放：请求被取消（用户关掉标签页）时线程不会跟着停，
    它会继续把预算烧完。那时如果标记已经放开，这个人立刻又能点一次，两个线程
    一起烧——正好是这道闸门要防的事。见 solve_guard.release。
    """
    import asyncio

    def _work():
        try:
            return run_solve(project, before)
        finally:
            solve_guard.release(user_id)

    return await asyncio.to_thread(_work)


# --------------------------------------------------------------------- 协作

def _collab(row: ScheduleProject, db: Session, viewer: Identity) -> dict:
    held = store.lock_holder(row)
    return {
        "owner_id": str(row.ruc_user_id),
        "owner_nickname": row.owner_nickname or "（未记录）",
        "is_owner": row.ruc_user_id == viewer.user_id,
        "members": [
            {"user_id": str(m.user_id), "nickname": m.nickname or "（未记录）",
             "joined_at": m.joined_at, "is_me": m.user_id == viewer.user_id}
            for m in store.list_members(db, row.id)
        ],
        # 谁正占着编辑权。None = 没人占（或者占用已经过期）。
        "editing": None if held is None else {
            "user_id": str(held[0]), "nickname": held[1] or "另一个人",
            "since": row.editing_at, "is_me": held[0] == viewer.user_id,
        },
        "lock_minutes": int(store.LOCK_TTL.total_seconds() // 60),
        "max_members": store.MAX_MEMBERS,
    }


@router.get("/projects/{project_id}/collab")
async def read_collab(
    project_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    row = _owned(project_id, identity, db, "read")
    return _collab(row, db, identity)


@router.post("/projects/{project_id}/lock")
async def take_lock(
    project_id: uuid.UUID,
    force: bool = Query(default=False, description="接管别人的编辑权"),
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    """占住编辑权（或续期）。`force=1` 接管别人的。

    接管这个口子是给「他挂着页面走了」准备的：占用五分钟才过期，而人可能只是
    开着标签页去吃饭。没有它，另一个人只能干等——那种等待没有任何用户价值。
    接管不是无痕的：接管之后的每一次写入，历史版本里记的都是接管者。
    """
    row = _owned(project_id, identity, db, "write")
    store.claim_lock(db, row, identity, force=force)
    return _collab(row, db, identity)


@router.delete("/projects/{project_id}/lock")
async def drop_lock(
    project_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    row = _owned(project_id, identity, db, "read")
    store.release_lock(db, row, identity)
    return _collab(row, db, identity)


@router.get("/projects/{project_id}/invites")
async def list_invites(
    project_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    row = _owned(project_id, identity, db, "read")
    now = datetime.now(UTC)
    return {
        "items": [
            {
                "id": str(i.id), "token": i.token,
                "created_nickname": i.created_nickname or "（未记录）",
                "created_at": i.created_at, "expires_at": i.expires_at,
                "used_count": i.used_count,
                "alive": i.revoked_at is None and i.expires_at > now,
            }
            for i in store.list_invites(db, row.id)
        ],
        "ttl_days": int(store.INVITE_TTL.total_seconds() // 86400),
    }


@router.post("/projects/{project_id}/invites", status_code=status.HTTP_201_CREATED)
async def create_invite(
    project_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    """生成一条邀请链接。

    ⚠️ **拿到链接就能看到全员的身份证号和联系方式。** 所以有效期只有几天、
    随时可撤销，而且列表里能看见每条是谁建的、被用过几次。
    """
    row = _owned(project_id, identity, db, "write")
    invite = store.create_invite(db, row, identity)
    return {"token": invite.token, "expires_at": invite.expires_at}


@router.delete("/projects/{project_id}/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    project_id: uuid.UUID,
    invite_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> Response:
    row = _owned(project_id, identity, db, "write")
    store.revoke_invite(db, row.id, invite_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/projects/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> Response:
    """移出一个协作者。自己退出也走这条。

    **创建者移不掉**：他不是 members 里的行，而是项目的 ruc_user_id。
    """
    row = _owned(project_id, identity, db, "write")
    store.remove_member(db, row.id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invites/{token}/accept")
async def accept_invite(
    token: str,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> ProjectSummary:
    """拿链接加入。**不在 /projects 下面**——加入之前你还不是这个项目的人，
    走那条路径会先被 owner 过滤挡掉。"""
    row = store.accept_invite(db, token, identity)
    return _summary(row, identity)


# --------------------------------------------------------------------- 历史版本

def _version_out(row) -> ProjectVersion:
    return ProjectVersion(
        id=row.id, version=row.version, action=row.action,
        actor_id=row.actor_id, actor_nickname=row.actor_nickname or "（未记录）",
        created_at=row.created_at, saved_at=row.saved_at,
        # 空 action 的那些是攒出来的；真攒到了才算「合并」，一次就落盘的不算
        merged=not row.action and row.saved_at > row.created_at,
    )


@router.get("/projects/{project_id}/versions")
async def list_versions(
    project_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    """历史版本列表。**不含 data**——那是几十 MB 的东西。"""
    row = _owned(project_id, identity, db, "read")
    return {
        "items": [_version_out(v) for v in store.list_versions(db, row.id)],
        "max_kept": store.MAX_VERSIONS,
        "window_minutes": int(store.MANUAL_WINDOW.total_seconds() // 60),
    }


@router.get("/projects/{project_id}/versions/{version_id}")
async def read_version(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    """某一版的内容 + 派生视图。**只读，不落库**——先看清楚再决定要不要恢复。

    ⚠️ 这里会把那一版的完整名单发出去（含联系方式），和读当前项目是同一档，
    所以走的也是同一道 owner 过滤。
    """
    row = _owned(project_id, identity, db, "read")
    snapshot = store.get_version(db, row.id, version_id)
    project = Project.from_dict(snapshot.data or {})
    return {
        "meta": _version_out(snapshot),
        "data": project.to_dict(),
        "views": derive(project),
    }


@router.post("/projects/{project_id}/versions/{version_id}/restore")
async def restore_version(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> ProjectDetail:
    """恢复到某一版。

    **是「新写一版，内容等于旧版」，不是把版本号回退**（理由见 store.restore_version）。
    恢复本身也会进历史，所以恢复错了还能再恢复回来。
    """
    row = _owned(project_id, identity, db, "write")
    snapshot = store.get_version(db, row.id, version_id)
    updated = store.restore_version(db, row, snapshot, identity)
    return _detail(updated, identity)


# --------------------------------------------------------------------- 规则试算

@router.post("/projects/{project_id}/rules/preview")
async def preview_rules(
    project_id: uuid.UUID,
    payload: PreviewRules,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> dict:
    """拿一份规则配置在本项目数据上试算一次，**不落库**，只报后果。

    为什么要有这一步：9 项可开关可排序意味着几十万种组合，贪心算法在某些组合下
    会给出明显糟糕的结果（比如「增量稳定」置顶 + 「最低人数」关掉，会出现一堆
    岗位达不到最低线而算法认为已经没有进展了）。而用户不会认为是自己配错了，
    只会认为是程序算错了。所以拖完顺序不能直接去排班，先看一眼后果。

    走的是求解器本身，不是另写一套估算——估算和实际不一致的话，这一步反而害人。
    """
    row = _owned(project_id, identity, db, "preview")
    project = store.load_core(row)
    store.check_size(project)
    try:
        project.rules = rulecfg.normalize(payload.rules)
    except rulecfg.RuleError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    before = project.current_pairs()
    await solve_guard.acquire(identity.user_id)
    try:
        outcome = await _run(project, before, identity.user_id)
    except SolveTimeout as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    project.assignments = outcome.assignments
    stats = position_stats(project)
    rows, counts = diff_pairs(before, project.current_pairs())
    placed = {a.vol_key for a in outcome.assignments}
    return {
        "rules": project.rules,
        "describe": rulecfg.describe(project.rules),
        "below_min": [s["pid"] for s in stats
                      if s["min_quota"] and s["filled"] < s["min_quota"]],
        "leader_short": [s["pid"] for s in stats
                         if s["leader_quota"] and s["leader_filled"] < s["leader_quota"]],
        "stranded": sum(1 for vk in project.desired_map() if vk not in placed),
        "moved": int(counts.get("换岗", 0)) + int(counts.get("移除", 0)),
        "changes": len(rows),
        "degraded": outcome.degraded,
        "seconds": round(outcome.seconds, 2),
    }


# --------------------------------------------------------------------- 导出

@router.get("/projects/{project_id}/export")
async def export_project(
    project_id: uuid.UUID,
    identity: Identity = Depends(require_login),
    db: Session = Depends(get_db),
) -> Response:
    """导出 5 张表合成的 xlsx。

    ⚠️ 这份文件**含身份证号和联系方式**。服务器上不留副本,直接当响应体发出去。
    """
    row = _owned(project_id, identity, db, "export")
    project = store.load_core(row)
    data = export_xlsx(project)
    filename = f"schedule-{row.id}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sample")
async def download_sample(_: Identity = Depends(require_login)) -> Response:
    """示例表格：岗位表 + 报名表两张 sheet。里面的身份证号是编的。"""
    return Response(
        content=sample_xlsx(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="schedule-sample.xlsx"'},
    )
