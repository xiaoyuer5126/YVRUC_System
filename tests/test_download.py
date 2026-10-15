"""下载：响应头里的文件名给两份写法。

安卓上不少浏览器把下载交给系统下载器，它只认普通的 `filename="..."`；
中文名进不了那一份，也不能只给 `filename*=UTF-8''...`。
"""
from __future__ import annotations

import io
import re
from urllib.parse import unquote

from conftest import dl_url


def _names(resp) -> tuple[str, str]:
    """响应头里的两个文件名：(普通写法, 带编码写法)，后者已解回中文。"""
    cd = resp.headers["content-disposition"]
    plain = re.search(r'filename="([^"]*)"', cd)
    starred = re.search(r"filename\*=UTF-8''([^;]+)", cd)
    assert plain and starred, cd
    return plain.group(1), unquote(starred.group(1))


def test_材料下载的文件名有普通写法兜底(client, admin, enrolled_activity):
    act, shift, _u = enrolled_activity
    r = client.post("/api/staff/materials", headers=admin,
                    data={"owner_type": "position",
                          "owner_id": shift.positions[0].id,
                          "title": "岗前培训手册.pdf"},
                    files={"file": ("培训.pdf", io.BytesIO(b"x" * 8),
                                    "application/pdf")})
    assert r.status_code == 200, r.text

    r = client.get(dl_url(client, admin, f"/api/materials/{r.json()['id']}"))
    assert r.status_code == 200
    plain, starred = _names(r)
    assert starred == "岗前培训手册.pdf"
    assert plain.isascii() and plain.endswith(".pdf")


def test_导出表格的文件名有普通写法兜底(client, admin, enrolled_activity):
    act, _shift, _u = enrolled_activity
    r = client.get(dl_url(client, admin,
                          f"/api/staff/activities/{act.id}/export.xlsx"))
    assert r.status_code == 200
    plain, starred = _names(r)
    assert plain.isascii() and plain.endswith(".xlsx")
    assert "排班表" in starred and starred.endswith(".xlsx")
