"""学习材料：场次/岗位下的培训文档与视频链接。

文档自己存（上限见 config.MAX_MATERIAL_BYTES），视频走链接——自托管大视频要
分片上传、断点续传和对象存储，代价远超收益，指向学校平台或网盘即可。
"""
from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from .. import config
from ..audit import log as audit_log
from ..database import db_session
from ..deps import current_user_download, require_manager
from ..models import Activity, Attachment, Position, Shift, User, uid
from ..perm import ensure_activity_manager, is_activity_manager

router = APIRouter(prefix="/api", tags=["materials"])

MATERIAL_DIR = config.DATA_DIR / "materials"
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
    """校验挂载对象存在，返回 (activity_id, 展示名)。"""
    if owner_type == "shift":
        row = db.get(Shift, owner_id)
        if row is None:
            raise HTTPException(404, "场次不存在")
        return row.activity_id, row.name
    if owner_type == "position":
        row = db.get(Position, owner_id)
        if row is None:
            raise HTTPException(404, "岗位不存在")
        return row.shift.activity_id, row.name
    raise HTTPException(400, "挂载对象只能是场次或岗位")


@router.post("/staff/materials")
async def upload_material(owner_type: str = Form(...), owner_id: str = Form(...),
                          title: str = Form(""), file: UploadFile = File(...),
                          db: OrmSession = Depends(db_session),
                          user: User = Depends(require_manager)):
    activity_id, owner_name = _owner(db, owner_type, owner_id)
    ensure_activity_manager(db, user, activity_id)

    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in EXT_TYPES:
        raise HTTPException(400, f"不支持的文件类型（可上传 "
                                 f"{'/'.join(sorted(EXT_TYPES))}）；"
                                 f"视频请改用「添加链接」")
    data = await file.read()
    if len(data) > config.MAX_MATERIAL_BYTES:
        limit = config.MAX_MATERIAL_BYTES // (1024 * 1024)
        raise HTTPException(400, f"文件不能超过 {limit}MB；"
                                 f"视频等大文件请改用「添加链接」")
    MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid4().hex}.{ext}"
    (MATERIAL_DIR / stored).write_bytes(data)

    row = Attachment(id=uid("att"), owner_type=owner_type, owner_id=owner_id,
                     activity_id=activity_id, kind="file",
                     title=(title.strip() or file.filename or stored)[:160],
                     stored_name=stored, size=len(data), uploaded_by=user.id)
    db.add(row)
    audit_log(db, actor=user, action="material", target_type="material",
              target_id=row.id, target_label=f"{owner_name}·{row.title}",
              activity_id=activity_id,
              summary=f"上传学习材料《{row.title}》（{len(data) // 1024}KB）")
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
              summary=f"添加学习材料链接《{row.title}》")
    db.commit()
    return {"id": row.id, "title": row.title}


@router.delete("/staff/materials/{material_id}")
def delete_material(material_id: str, db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    row = db.get(Attachment, material_id)
    if row is None:
        raise HTTPException(404, "材料不存在")
    ensure_activity_manager(db, user, row.activity_id)
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
    act = db.get(Activity, row.activity_id) if row.activity_id else None
    if act is not None and act.status == "draft" and not is_activity_manager(
            db, user, act.id):
        raise HTTPException(404, "材料不存在")
    path = MATERIAL_DIR / row.stored_name
    if not path.is_file():
        raise HTTPException(404, "文件已丢失，请联系负责老师重新上传")
    ext = row.stored_name.rsplit(".", 1)[-1]
    return FileResponse(path, media_type=EXT_TYPES.get(ext, "application/octet-stream"),
                        filename=row.title)
