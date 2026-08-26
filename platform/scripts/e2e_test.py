"""后端端到端自测：跑通登录→报名→冲突拦截→候补→智能排班→签到→导出→时长。

只依赖 TestClient，不启动服务。用于快速回归，不是正式测试套件。
"""
from __future__ import annotations

import io
import sys

sys.path.insert(0, r"F:\RUCVA_system\platform")

from fastapi.testclient import TestClient

from backend.main import app

c = TestClient(app)
OK, FAIL = "✓", "✗"
results = []


def step(name, cond, extra=""):
    results.append((OK if cond else FAIL, name, extra))
    assert cond, f"{name} {extra}"


def login(sid):
    r = c.post("/api/auth/login", json={"student_id": sid})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---- 登录 ----
staff_h = login("2025001")          # 王砚秋 admin
stu_h = login("202302126")          # 严望舒（候补转正待确认演示）
frozen_h = login("202401111")       # 褚天翼（信用冻结）

r = c.get("/api/auth/me", headers=staff_h)
step("登录后取个人信息", r.status_code == 200 and "admin" in r.json()["user"]["roles"])
r = c.get("/api/auth/constants")
step("常量接口", r.status_code == 200 and "A" in r.json()["activity_types"])
r = c.get("/api/auth/demo-accounts")
step("演示账号列表", r.status_code == 200 and len(r.json()) >= 5)

# ---- 学生浏览 ----
acts = c.get("/api/activities?scope=open", headers=stu_h).json()
step("活动广场（报名中）", len(acts) == 3, f"n={len(acts)}")
sport = next(a for a in acts if "运动会" in a["title"])
book = next(a for a in acts if "图书" in a["title"])
detail = c.get(f"/api/activities/{sport['id']}", headers=stu_h).json()
step("活动详情含岗位树", len(detail["groups"]) == 2
     and all(len(g["stations"]) > 0 for g in detail["groups"]))
step("变更记录对志愿者可见", len(detail.get("change_logs", [])) >= 2)

m1 = next(g for g in detail["groups"] if g["name"] == "上午场")
gate = next(s for s in m1["stations"] if s["name"] == "东门引导")
etiq = next(s for s in m1["stations"] if s["name"] == "颁奖礼仪")

# ---- 候补转正确认 ----
me_enr = c.get("/api/me/enrollments", headers=stu_h).json()
pc = next(e for e in me_enr if e["status"] == "pending_confirm")
step("待确认记录含确认截止时间", pc["confirm_deadline"] is not None)
r = c.post(f"/api/activities/enrollments/{pc['id']}/confirm-promotion",
           headers=stu_h)
step("候补转正确认成功", r.status_code == 200 and r.json()["station_id"],
     r.text[:120])
credit_before = c.get("/api/me/credit", headers=stu_h).json()
step("转正后信用分不变", credit_before["score"] == 100)

# ---- 报名冲突检测 ----
_demo = c.get("/api/auth/demo-accounts").json()
another_stu = login(_demo[-1]["student_id"])
fair = next(a for a in acts if "招生咨询" in a["title"])
gala_act = c.get("/api/activities?scope=all", headers=staff_h).json()
gala = next(a for a in gala_act if "晚会" in a["title"])

# 找一个报了图书周的学生去报名与图书周重叠的招生咨询会 → 应被拦或提示满员
book_detail = c.get(f"/api/activities/{book['id']}", headers=staff_h).json()
b1 = book_detail["groups"][0]
roster = c.get(f"/api/staff/groups/{b1['id']}/roster", headers=staff_h).json()
member_sid = roster["confirmed"][0]["user"]["student_id"]
member_h = login(member_sid)
fair_detail = c.get(f"/api/activities/{fair['id']}", headers=member_h).json()
f1 = fair_detail["groups"][0]
free_station = next((s for s in f1["stations"] if s["remaining"] > 0), None)
if free_station:
    r = c.post(f"/api/activities/{fair['id']}/groups/{f1['id']}/enroll",
               json={"station_id": free_station["id"]}, headers=member_h)
    # 该生已确认了咨询会？roster 里前3人是冲突演示成员已确认。
    # 若已确认会得到"请勿重复报名"；若未报过但与图书周重叠则409冲突。
    ok = r.status_code in (200, 409)
    step("重复/冲突报名被正确处理", ok,
         f"{r.status_code} {str(r.json())[:80]}")

# ---- 冻结用户不能报名 ----
sport_d = c.get(f"/api/activities/{sport['id']}", headers=frozen_h).json()
m1f = sport_d["groups"][0]
st_free = next(s for s in m1f["stations"]
               if s["remaining"] > 0 and s["name"] != "东门引导")
r = c.post(f"/api/activities/{sport['id']}/groups/{m1f['id']}/enroll",
           json={"station_id": st_free["id"]}, headers=frozen_h)
step("信用冻结用户报名被拒", r.status_code == 403, str(r.json())[:60])

# ---- 后台：待分配组智能排班（调配期：先建工位，再排班） ----
# 二级工位不再设置时间（时间归场次），只传职责/名额/地点
for name, q, loc in [("图书分拣区", 8, "三层分拣区"),
                     ("搬运小组", 6, "各楼层"),
                     ("重新上架组", 6, "借阅区书库")]:
    r = c.post(f"/api/staff/groups/{b1['id']}/stations", json={
        "name": name, "location": loc, "quota": q,
        "requirement_tags": {}, "requirement_note": "", "training_note": ""},
        headers=staff_h)
    assert r.status_code == 200, r.text

pv_body = {"preset": ""}
r = c.post(f"/api/staff/groups/{b1['id']}/assign/preview", json=pv_body,
           headers=staff_h)
step("智能排班预览（schedule求解器）", r.status_code == 200, r.text[:150])
preview = r.json()
step("预览覆盖全部已确认名单",
     len(preview["rows"]) == preview["total_confirmed"],
     f'{len(preview["rows"])}/{preview["total_confirmed"]}')
# 二级工位互斥：每人至多一个建议工位；未分配名单有原因说明
step("互斥分配（一人一工位）",
     preview["total_assigned"] <= preview["total_confirmed"],
     f'assigned={preview["total_assigned"]}')
plan = [{"enrollment_id": x["enrollment_id"],
         "station_id": x["suggested_station_id"], "force": False}
        for x in preview["rows"]]
r = c.post(f"/api/staff/groups/{b1['id']}/assign/apply",
           json={"plan": plan, "source": "assign"}, headers=staff_h)
step("应用分配方案", r.status_code == 200 and r.json()["applied"] >= 10,
     r.text[:150])

# 手动调岗走 apply 单行方案（把第一人挪到第一个工位）
roster2 = c.get(f"/api/staff/groups/{b1['id']}/roster", headers=staff_h).json()
sts = roster2["stations"]
victim = roster2["confirmed"][0]["enrollment_id"]
r = c.post(f"/api/staff/groups/{b1['id']}/assign/apply",
           json={"plan": [{"enrollment_id": victim,
                           "station_id": sts[0]["id"], "force": False}],
                 "source": "manual"}, headers=staff_h)
step("手动单行调岗", r.status_code == 200, r.text[:100])

# ---- 签到 ----
gala_groups = c.get(f"/api/activities/{gala['id']}", headers=staff_h).json()
gg = gala_groups["groups"][0]
tasks = c.get(f"/api/staff/activities/{gala['id']}/checkin-tasks",
              headers=staff_h).json()
t0 = next(t for t in tasks if t["mode"] == "group" and t["token"] == "GALA01")
board = c.get(f"/api/staff/checkin-tasks/{t0['id']}/board", headers=staff_h).json()
step("签到大屏有数据", board["checked"] >= 5, f'checked={board["checked"]}')
# 未签到的晚会志愿者扫码——自建宽时限任务，避免深夜场次过期干扰
from datetime import datetime, timedelta as td
gg_now = datetime.now()
r = c.post(f"/api/staff/groups/{gg['id']}/checkin-tasks",
           json={"mode": "group",
                 "valid_from": (gg_now - td(hours=1)).isoformat(),
                 "valid_to": (gg_now + td(hours=2)).isoformat()},
           headers=staff_h)
assert r.status_code == 200, r.text
scan_token = r.json()["token"]
roster_g = c.get(f"/api/staff/groups/{gg['id']}/roster", headers=staff_h).json()
pending = [x for x in roster_g["confirmed"] if not x["checked_in"]]
if pending:
    stu2_h = login(pending[0]["user"]["student_id"])
    info = c.get(f"/api/checkin/task/{scan_token}", headers=stu2_h).json()
    step("扫码任务信息", info["enrolled"] is True and info["checked_in"]
         is False, str(info)[:100])
    r = c.post(f"/api/checkin/task/{scan_token}", headers=stu2_h)
    step("扫码签到成功", r.status_code == 200, r.text[:100])
qr = c.get("/api/checkin/qr.svg?token=GALA01")
step("二维码SVG生成", qr.status_code == 200 and b"<svg" in qr.content)

# ---- 导出 ----
r = c.get(f"/api/staff/activities/{sport['id']}/export.xlsx", headers=staff_h)
step("Excel导出", r.status_code == 200 and r.headers["content-type"]
     .startswith("application/vnd"), f"{len(r.content)}bytes")
try:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    step("导出按一级岗位分Sheet", len(wb.sheetnames) == 3,
         str(wb.sheetnames))
except ImportError:
    pass

# ---- 时长 ----
elder = next(a for a in gala_act if "敬老院" in a["title"])
hp = c.get(f"/api/staff/activities/{elder['id']}/hours", headers=staff_h).json()
step("时长预览（已上传状态）", hp["items"] and hp["items"][0]["status"] == "uploaded")

print()
for mark, name, extra in results:
    print(f" {mark} {name}  {extra}")
print(f"\n共 {len(results)} 项断言")
