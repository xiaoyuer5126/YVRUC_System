"""下载响应的文件名写法。

浏览器的下载器认两种文件名：`filename="..."` 只放得下 ASCII 字符，`filename*=UTF-8''...`
把中文转成百分号编码。两个一起给：认带星号那种的浏览器照旧显示中文名，把下载交给系统
下载器的浏览器（安卓上不少）只认普通写法，少给这一份就取不到文件名、直接报下载失败。
"""
from __future__ import annotations

import re
from urllib.parse import quote

from fastapi.responses import FileResponse, StreamingResponse

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ZIP = "application/zip"


def ascii_name(filename: str) -> str:
    """ASCII 兜底名：中文等非 ASCII 字符换成一个下划线，扩展名照留。

    整名都是中文时压不出可用名字，用 download 顶上。引号、反斜杠和斜杠留在名字
    里会破坏这个头，去掉。
    """
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    ext = re.sub(r'[^!-~]|["\\/]', "", ext)
    stem = re.sub(r'[^!-~]+', "_", stem)
    stem = re.sub(r'["\\/]+', "", stem)
    stem = re.sub(r"[\s_]+", "_", stem).strip(" _.-")
    if not stem:
        stem = "download"
    return f"{stem}.{ext}" if ext else stem


def content_disposition(filename: str, kind: str = "attachment") -> str:
    return (f'{kind}; filename="{ascii_name(filename)}"; '
            f"filename*=UTF-8''{quote(filename)}")


def headers(filename: str, kind: str = "attachment") -> dict[str, str]:
    return {"Content-Disposition": content_disposition(filename, kind)}


def file_response(path, filename: str, *, media_type: str | None = None,
                  kind: str = "attachment") -> FileResponse:
    """发一个磁盘上的文件，文件名自己给两份。

    不用 FileResponse 的 filename 参数：它对中文名只发带星号那一份。
    """
    return FileResponse(path, media_type=media_type,
                        headers=headers(filename, kind))


def stream_response(buf, filename: str, media_type: str, *,
                    kind: str = "attachment") -> StreamingResponse:
    """发内存里的表格或压缩包。"""
    return StreamingResponse(buf, media_type=media_type,
                             headers=headers(filename, kind))
