"""智能起草的附件：把管理者上传的文件变成模型读得了的东西。

文档在本地转文字，图片原样发给能识图的模型（DeepSeek 的 deepseek-flash 收
JPEG / PNG / GIF / WebP，文档类它不收）：
· docx：正文段落 + 表格（一行一行，单元格用「 | 」隔开），插在里面的图片也取出来；
· xlsx：每个工作表一行一行转成文字，插在表里的图片也取出来；
· pdf：取文字层。取不到字的多半是扫描件，请对方截图后当图片传；
· 图片：直接发。

为什么不把文档也渲染成图片：要在容器里装 LibreOffice 和中文字体，镜像大几百 MB，
每份文档转一次好几秒；密集的排班表截成图，时间、人数反而容易认错。

文件只在内存里过一遍，不落盘、不入库。
"""
from __future__ import annotations

import base64
import zipfile
from io import BytesIO
from pathlib import PurePosixPath

from fastapi import HTTPException, UploadFile

from . import config

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
DOC_TYPES = {".docx", ".xlsx", ".pdf"}
OLD_FORMATS = {".doc", ".xls", ".wps", ".et", ".ppt", ".pptx"}


class Extracted:
    def __init__(self):
        self.texts: list[str] = []      # 「【附件：文件名】\n内容」
        self.images: list[str] = []     # data: URL
        self.notes: list[str] = []      # 给管理者看的提醒（读不了的图、扫描件……）

    def add_image(self, data: bytes, mime: str, where: str) -> None:
        if len(self.images) >= config.AI_IMAGE_MAX:
            self.notes.append(f"图片超过 {config.AI_IMAGE_MAX} 张，{where}没有发给模型")
            return
        if len(data) > config.AI_IMAGE_MAX_BYTES:
            self.notes.append(f"{where}太大，没有发给模型")
            return
        self.images.append(f"data:{mime};base64,{base64.b64encode(data).decode()}")


def _check_zip(data: bytes, name: str) -> None:
    """打开 docx / xlsx 之前，按包里登记的解压后大小把解压炸弹挡在门外。

    登记的大小信得过：zipfile 解压时最多吐出登记的那么多字节，多出来的截掉并
    在校验 CRC 时报错，所以谎报大小也解不出更多东西来。不是 zip 的（坏文件）
    在这里抛 BadZipFile，由调用方当作「读不了」。
    """
    total = 0
    with zipfile.ZipFile(BytesIO(data)) as z:
        for info in z.infolist():
            size = info.file_size
            total += size
            bomb = (size > config.AI_ZIP_RATIO_MIN_BYTES
                    and size > config.AI_ZIP_RATIO_MAX * max(info.compress_size, 1))
            if (size > config.AI_ZIP_ENTRY_MAX_BYTES or bomb
                    or total > config.AI_ZIP_TOTAL_MAX_BYTES):
                raise HTTPException(400, f"「{name}」解压后太大或结构异常，读不了。"
                                         "请删掉不需要的内容、另存一份再传")


def _media(data: bytes, folder: str, out: Extracted, name: str) -> None:
    """docx / xlsx 本质是压缩包，插进去的图片在 media 目录里。"""
    vector = 0
    with zipfile.ZipFile(BytesIO(data)) as z:
        for item in sorted(z.namelist()):
            if not item.startswith(folder):
                continue
            ext = PurePosixPath(item).suffix.lower()
            if ext in IMAGE_TYPES:
                # 先看解压后多大再读：文档本身只有 10MB，里面塞的图能解出几百 MB，
                # 读进来再判大小的话，这一下内存就顶上去了
                if z.getinfo(item).file_size > config.AI_IMAGE_MAX_BYTES:
                    out.notes.append(f"「{name}」里的图片太大，没有发给模型")
                    continue
                out.add_image(z.read(item), IMAGE_TYPES[ext], f"「{name}」里的图片")
            elif ext in (".emf", ".wmf"):
                vector += 1
    if vector:
        # Word 里粘贴的 Excel 表格常是这种矢量图，不装 LibreOffice 读不了
        out.notes.append(f"「{name}」里有 {vector} 张矢量图读不了，需要的话截图后作为图片上传")


def _docx(data: bytes) -> str:
    from docx import Document

    doc = Document(BytesIO(data))
    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for t in doc.tables:
        lines.append("")
        for row in t.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _xlsx(data: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets:
        lines.append(f"【工作表：{ws.title}】")
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v).strip() for v in row]
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _pdf(data: bytes, name: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    if len(reader.pages) > config.AI_PDF_MAX_PAGES:
        raise HTTPException(400, f"「{name}」超过 {config.AI_PDF_MAX_PAGES} 页，"
                                 "请只保留需要的几页再传")
    return "\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()


def extract(files: list[UploadFile]) -> Extracted:
    """逐个读附件。格式不认识、太大、读坏了都直接报给管理者，不悄悄跳过。"""
    out = Extracted()
    if len(files) > config.AI_FILE_COUNT:
        raise HTTPException(400, f"一次最多传 {config.AI_FILE_COUNT} 个文件")
    for f in files:
        name = f.filename or "附件"
        ext = PurePosixPath(name).suffix.lower()
        if ext in OLD_FORMATS:
            raise HTTPException(400, f"「{name}」是旧格式或演示文稿，请另存为 docx / xlsx / pdf 再传")
        if ext not in DOC_TYPES and ext not in IMAGE_TYPES:
            raise HTTPException(400, f"「{name}」的格式读不了，支持 docx、xlsx、pdf 和图片")
        # 同步读：起草接口是普通 def（在线程池里跑），等模型那一分钟不能卡住事件循环
        data = f.file.read(config.AI_FILE_MAX_BYTES + 1)
        if len(data) > config.AI_FILE_MAX_BYTES:
            raise HTTPException(413, f"「{name}」太大了，单个文件不超过 "
                                     f"{config.AI_FILE_MAX_BYTES // 1024 // 1024} MB")
        if ext in IMAGE_TYPES:
            out.add_image(data, IMAGE_TYPES[ext], f"「{name}」")
            continue
        try:
            if ext in (".docx", ".xlsx"):
                _check_zip(data, name)
            if ext == ".docx":
                text = _docx(data)
                _media(data, "word/media/", out, name)
            elif ext == ".xlsx":
                text = _xlsx(data)
                _media(data, "xl/media/", out, name)
            else:
                text = _pdf(data, name)
                if not text:
                    raise HTTPException(400, f"「{name}」取不到文字（多半是扫描件），"
                                             "请截图后作为图片上传")
        except HTTPException:
            raise
        except Exception:       # 坏文件、加密文件：各个库抛的异常五花八门
            raise HTTPException(400, f"「{name}」读不了，可能文件损坏或加了密码")
        if text:
            out.texts.append(f"【附件：{name}】\n{text}")
    return out
