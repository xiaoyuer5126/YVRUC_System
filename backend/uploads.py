"""附件上传：校验格式与大小，落盘并写一条 Attachment。

意见反馈的截图、时长补登的证明材料与活动照片共用这一份。文件头判断各抄一套
的话，迟早只有一处会被改到——而且改的往往是后写的那份。

读与写分成两步：先全部读完、校验完再落盘。第三份不合格时，前两份不该已经
写在磁盘上（调用方拿到返回值再写，见 save_files）。
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session as OrmSession

from . import config
from .models import Attachment, uid

# 扩展名 → 下载时回给浏览器的类型
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg",
               "jpeg": "image/jpeg", "webp": "image/webp"}
EXT_TYPES = {**IMAGE_TYPES, "pdf": "application/pdf"}


def is_image(ext: str, data: bytes) -> bool:
    """看文件头，不光看扩展名：改个后缀的 html 当图片存下来，下载时按图片类型
    回给浏览器问题不大，但「仅图片」就该名副其实。"""
    if ext == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in ("jpg", "jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    if ext == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def is_pdf(data: bytes) -> bool:
    return data.startswith(b"%PDF-")


async def read_files(files: list[UploadFile], *, label: str, exts: dict,
                     max_count: int, max_bytes: int) -> list[tuple[str, str, bytes]]:
    """读完并校验，返回 [(扩展名, 原始文件名, 内容)]。"""
    if len(files) > max_count:
        raise HTTPException(400, f"{label}最多 {max_count} 份")
    limit_mb = max_bytes // (1024 * 1024)
    out: list[tuple[str, str, bytes]] = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower().lstrip(".")
        if ext not in exts:
            raise HTTPException(400, f"{label}只支持 {'、'.join(sorted(exts))} 格式")
        # 多读一个字节就够判断超没超，不必把一个超大文件整个读进内存
        data = await f.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(400, f"{label}单份不能超过 {limit_mb}MB")
        if ext in IMAGE_TYPES and not is_image(ext, data):
            raise HTTPException(400, f"{label}里有一份不是图片（改后缀也不行）")
        if ext == "pdf" and not is_pdf(data):
            raise HTTPException(400, f"{label}里有一份不是 PDF")
        out.append((ext, _title(f.filename or f"{label}.{ext}"), data))
    return out


TITLE_MAX = 160      # Attachment.title 的长度


def _title(name: str) -> str:
    """原始文件名放进 TITLE_MAX 个字：太长时截文件名主体，扩展名留着。

    页面按文件名后缀分辨 PDF 和图片，截掉 .pdf 的话，PDF 会被当成图片显示成坏图。
    """
    if len(name) <= TITLE_MAX:
        return name
    suffix = Path(name).suffix
    return name[:TITLE_MAX - len(suffix)] + suffix


def save_files(db: OrmSession, files, *, owner_type: str, owner_id: str,
               activity_id: str = "", uploaded_by: str) -> list[Attachment]:
    """落盘并写 Attachment（不 commit，由调用方决定事务边界）。"""
    rows: list[Attachment] = []
    if files:
        config.MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    for ext, title, data in files:
        stored = f"{uuid4().hex}.{ext}"
        config.MATERIAL_DIR.joinpath(stored).write_bytes(data)
        row = Attachment(id=uid("att"), owner_type=owner_type, owner_id=owner_id,
                         activity_id=activity_id, kind="file", title=title,
                         stored_name=stored, size=len(data), uploaded_by=uploaded_by)
        db.add(row)
        rows.append(row)
    return rows
