"""学习材料：场次/岗位下的培训文档与视频链接。

文档自己存（上限见 config.MAX_MATERIAL_BYTES），视频走链接——自托管大视频要
分片上传、断点续传和对象存储，代价远超收益，指向学校平台或网盘即可。
"""
from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from .. import config, downloads
from ..audit import log as audit_log
from ..database import db_session
from ..deps import current_user_download, require_manager
from ..liaison import SLOTS as PROJECT_SLOTS
from ..models import Activity, Attachment, Notification, Position, User, uid
from ..perm import ensure_activity_manager, is_activity_manager
from .activities import is_public
from ..stage import ensure_not_finished

router = APIRouter(prefix="/api", tags=["materials"])

MATERIAL_DIR = config.MATERIAL_DIR
# 这条接口管的材料只挂在岗位上（上传/加链接也只认 position）。其余 owner_type
# 各有自己的写入方：问卷附件 application、反馈截图 feedback、领取凭证 claim_proof/
# claim_photo、签到二维码、立项书/结项书（PROJECT_SLOTS）、模板 template、
# 通知附件 notify（upload_notify_file）。
MATERIAL_OWNER_TYPES = frozenset({"position"})
EXT_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "txt": "text/plain; charset=utf-8",
}


def _owner(db: OrmSession, owner_type: str, owner_id: str) -> tuple[str, str]:
    """校验挂载对象存在，返回 (activity_id, 展示名)。材料只挂在岗位上：
    培训内容是按岗位讲的，场次这一层没有自己的材料。"""
    if owner_type == "position":
        row = db.get(Position, owner_id)
        if row is None:
            raise HTTPException(404, "岗位不存在")
        return row.shift.activity_id, row.name
    raise HTTPException(400, "学习材料只能挂在岗位上")


async def _store(file: UploadFile, hint: str = "") -> tuple[str, bytes]:
    """校验类型和大小后存进 MATERIAL_DIR，返回 (存盘文件名, 内容)。"""
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in EXT_TYPES:
        raise HTTPException(400, f"不支持的文件类型（可上传 "
                                 f"{'/'.join(sorted(EXT_TYPES))}）{hint}")
    data = await file.read()
    if len(data) > config.MAX_MATERIAL_BYTES:
        limit = config.MAX_MATERIAL_BYTES // (1024 * 1024)
        raise HTTPException(400, f"文件不能超过 {limit}MB{hint}")
    MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid4().hex}.{ext}"
    (MATERIAL_DIR / stored).write_bytes(data)
    return stored, data


@router.post("/staff/materials")
async def upload_material(owner_type: str = Form(...), owner_id: str = Form(...),
                          title: str = Form(""), file: UploadFile = File(...),
                          db: OrmSession = Depends(db_session),
                          user: User = Depends(require_manager)):
    activity_id, owner_name = _owner(db, owner_type, owner_id)
    ensure_activity_manager(db, user, activity_id)
    ensure_not_finished(db.get(Activity, activity_id))

    stored, data = await _store(file, "；视频请改用「添加链接」")

    row = Attachment(id=uid("att"), owner_type=owner_type, owner_id=owner_id,
                     activity_id=activity_id, kind="file",
                     title=(title.strip() or file.filename or stored)[:160],
                     stored_name=stored, size=len(data), uploaded_by=user.id)
    db.add(row)
    audit_log(db, actor=user, action="material", target_type="material",
              target_id=row.id, target_label=f"{owner_name}·{row.title}",
              activity_id=activity_id,
              summary=f"上传学习材料《{row.title}》（{len(data) // 1024} KB）",
              visible=True)
    db.commit()
    return {"id": row.id, "title": row.title, "size": row.size}


class LinkBody(BaseModel):
    owner_type: str
    owner_id: str
    title: str
    url: str


@router.post("/staff/materials/link")
def add_link(body: LinkBody, db: OrmSession = Depends(db_session),
             user: User = Depends(require_manager)):
    activity_id, owner_name = _owner(db, body.owner_type, body.owner_id)
    ensure_activity_manager(db, user, activity_id)
    ensure_not_finished(db.get(Activity, activity_id))
    url = body.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "链接必须以 http:// 或 https:// 开头")
    if not body.title.strip():
        raise HTTPException(400, "请填写材料名称")
    row = Attachment(id=uid("att"), owner_type=body.owner_type,
                     owner_id=body.owner_id, activity_id=activity_id,
                     kind="link", title=body.title.strip()[:160],
                     url=url[:512], uploaded_by=user.id)
    db.add(row)
    audit_log(db, actor=user, action="material", target_type="material",
              target_id=row.id, target_label=f"{owner_name}·{row.title}",
              activity_id=activity_id,
              summary=f"添加学习材料链接《{row.title}》",
              visible=True)
    db.commit()
    return {"id": row.id, "title": row.title}


@router.delete("/staff/materials/{material_id}")
def delete_material(material_id: str, db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    row = db.get(Attachment, material_id)
    # 只删学习材料：挂在岗位上的。别的 owner_type 都各有归属和接口——问卷附件
    # 是报名者自己传的、反馈截图归提交人、领取凭证归本人、立项书/结项书一格只能
    # 换不能空。从这条接口删会删掉别人的东西，模板类不带活动还会直接报错。
    if row is None or row.owner_type not in MATERIAL_OWNER_TYPES:
        raise HTTPException(404, "材料不存在")
    ensure_activity_manager(db, user, row.activity_id)
    ensure_not_finished(db.get(Activity, row.activity_id))
    # 复制活动时文件是各存一份的，删记录就可以把文件一起删掉
    if row.kind == "file":
        (MATERIAL_DIR / row.stored_name).unlink(missing_ok=True)
    title, activity_id = row.title, row.activity_id
    db.delete(row)
    audit_log(db, actor=user, action="material", target_type="material",
              target_id=material_id, target_label=title,
              activity_id=activity_id, summary=f"删除学习材料《{title}》")
    db.commit()
    return {"ok": True}


@router.post("/staff/activities/{activity_id}/notify-files")
async def upload_notify_file(activity_id: str, file: UploadFile = File(...),
                             db: OrmSession = Depends(db_session),
                             user: User = Depends(require_manager)):
    """发通知的附件：先传上来拿到 id，发通知时随 file_ids 带上。

    传了没发的文件不挂在任何通知上，谁也下载不到，过一阵由定时任务删掉；
    留痕记在发通知那一下。
    """
    ensure_activity_manager(db, user, activity_id)
    if db.get(Activity, activity_id) is None:
        raise HTTPException(404, "活动不存在")
    stored, data = await _store(file)
    row = Attachment(id=uid("att"), owner_type="notify", owner_id=activity_id,
                     activity_id=activity_id, kind="file",
                     title=(file.filename or stored)[:160],
                     stored_name=stored, size=len(data), uploaded_by=user.id)
    db.add(row)
    db.commit()
    return {"id": row.id, "title": row.title, "size": row.size}


def _received(db: OrmSession, user: User, row: Attachment) -> bool:
    """这份通知附件有没有随某条通知发到这个人手上。"""
    notes = (db.query(Notification.data)
             .filter(Notification.user_id == user.id,
                     Notification.template_key == "custom",
                     Notification.created_at >= row.created_at).all())
    return any(row.id in {f.get("id") for f in (d or {}).get("files", [])}
               for (d,) in notes)


@router.get("/materials/{material_id}")
def download_material(material_id: str, db: OrmSession = Depends(db_session),
                      user: User = Depends(current_user_download)):
    """志愿者下载培训材料。

    走 current_user_download：这是 <a href> 直链，带不了 Authorization 头。
    登录即可下载——材料本身就是发给报名者看的；但筹备中的活动整体不对外，
    它的材料也不能漏出去。
    """
    row = db.get(Attachment, material_id)
    if row is None or row.kind != "file":
        raise HTTPException(404, "材料不存在")
    # 活动公开之前（草稿、审批中、待发布）它的材料也不对外：
    # 只按 draft 判断的话，交上去等审批的那一段时间材料就漏出去了
    act = db.get(Activity, row.activity_id) if row.activity_id else None
    if not row.activity_id:
        # 不挂活动的只有两种：立项书/结项书模板，发给发起方照着填，登录即可下载；
        # 意见反馈的截图有自己的接口（只给管理员和提交人），这里一律不给
        if row.owner_type != "template":
            raise HTTPException(404, "材料不存在")
    elif row.owner_type in PROJECT_SLOTS:
        # 立项书是内部材料：不管活动公没公开，只给管理者与审批线上的人
        # （值班人审的正是还没公开的活动，下面那条公开判断会把他挡掉）
        from ..liaison import can_see_project_files
        if act is None or not can_see_project_files(db, user, act):
            raise HTTPException(404, "材料不存在")
    elif row.owner_type == "notify":
        # 通知附件只给这场活动的管理者和收到这条通知的人
        if not (is_activity_manager(db, user, row.activity_id)
                or _received(db, user, row)):
            raise HTTPException(404, "材料不存在")
    elif act is None or (not is_public(act)
                         and not is_activity_manager(db, user, act.id)):
        raise HTTPException(404, "材料不存在")
    # 报名问卷里志愿者交的文件是个人材料：只有本人和这场活动的管理者能看
    if row.owner_type == "application" and row.uploaded_by != user.id \
            and not is_activity_manager(db, user, row.activity_id):
        raise HTTPException(404, "材料不存在")
    path = MATERIAL_DIR / row.stored_name
    if not path.is_file():
        raise HTTPException(404, "文件已丢失，请联系负责人重新上传")
    ext = row.stored_name.rsplit(".", 1)[-1]
    return downloads.file_response(
        path, row.title,
        media_type=EXT_TYPES.get(ext, "application/octet-stream"))
