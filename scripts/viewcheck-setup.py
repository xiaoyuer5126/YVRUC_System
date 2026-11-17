"""为 viewcheck.mjs 准备登录态。

用法（栈已起好、种子数据已导入）：
    python3 scripts/viewcheck-setup.py <输出目录>
"""
from __future__ import annotations

import json
import sys
from http.cookies import SimpleCookie
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8080"
PASSWORD = "devpass"
ADMIN_ID = "2025001"


SESSION_COOKIE = "yvruc_session"


def call(method: str, path: str, body=None, token: str = ""):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Cookie": f"{SESSION_COOKIE}={token}", "X-YVRUC": "1",
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def login(student_id: str) -> dict:
    """演示登录，返回 {token, user}。令牌只在 Set-Cookie 里，响应体里没有。"""
    req = urllib.request.Request(
        BASE + "/api/auth/demo-login", method="POST",
        data=json.dumps({"student_id": student_id, "password": PASSWORD}).encode(),
        headers={"X-YVRUC": "1", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        jar = SimpleCookie()
        for h in resp.headers.get_all("Set-Cookie") or []:
            jar.load(h)
        return {"token": jar[SESSION_COOKIE].value, "user": json.load(resp)["user"]}


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    admin = login(ADMIN_ID)
    users = call("GET", "/api/staff/users?size=100", token=admin["token"])["items"]
    student_id = next(u["student_id"] for u in users if u["profile_complete"])
    student = login(student_id)
    activities = call("GET", "/api/activities?status=published",
                      token=student["token"])["items"]

    (out / "atok.txt").write_text(admin["token"])
    (out / "tok.txt").write_text(student["token"])
    (out / "auser.json").write_text(json.dumps(admin["user"], ensure_ascii=False))
    (out / "suser.json").write_text(json.dumps(student["user"], ensure_ascii=False))
    (out / "act.txt").write_text(activities[0]["id"])

    # 只在特定活动里才有的页签与落地页：补记录活动的「名单导入」、统一发放
    # 服装的「志愿服装」与服装码落地页、当天签到中的签到码落地页
    def staff_act(title: str) -> str:
        q = urllib.parse.quote(title)
        return call("GET", f"/api/staff/activities?q={q}&size=5",
                    token=admin["token"])["items"][0]["id"]

    attire_act = staff_act("校运动会开幕式志愿服务")
    attire = call("POST", f"/api/staff/activities/{attire_act}/attire-tasks",
                  token=admin["token"])
    (out / "record_act.txt").write_text(staff_act("学院读书会志愿服务"))
    (out / "attire_act.txt").write_text(attire_act)
    (out / "attire_token.txt").write_text(attire["take"]["token"])
    (out / "checkin_token.txt").write_text("GALA01")   # 种子里晚会当天的统一签到码
    print(f"已写入 {out}（学生 {student['user']['name']}，活动 {activities[0]['title']}）")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/viewcheck")
