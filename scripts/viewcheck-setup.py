"""为 viewcheck.mjs 准备登录态。

用法（栈已起好、种子数据已导入）：
    python3 scripts/viewcheck-setup.py <输出目录>
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8080"
PASSWORD = "devpass"
ADMIN_ID = "2025001"


def call(method: str, path: str, body=None, token: str = ""):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    admin = call("POST", "/api/auth/demo-login",
                 {"student_id": ADMIN_ID, "password": PASSWORD})
    users = call("GET", "/api/staff/users", token=admin["token"])
    student_id = next(u["student_id"] for u in users if u["profile_complete"])
    student = call("POST", "/api/auth/demo-login",
                   {"student_id": student_id, "password": PASSWORD})
    activities = call("GET", "/api/activities?scope=open", token=student["token"])

    (out / "atok.txt").write_text(admin["token"])
    (out / "tok.txt").write_text(student["token"])
    (out / "auser.json").write_text(json.dumps(admin["user"], ensure_ascii=False))
    (out / "suser.json").write_text(json.dumps(student["user"], ensure_ascii=False))
    (out / "act.txt").write_text(activities[0]["id"])
    print(f"已写入 {out}（学生 {student['user']['name']}，活动 {activities[0]['title']}）")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/viewcheck")
