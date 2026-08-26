# 签退/时长闭环集成测试
import sys
from datetime import timedelta

sys.path.insert(0, r"F:\RUCVA_system\platform")
from fastapi.testclient import TestClient
from backend.main import app

c = TestClient(app)
ok = fail = 0


def step(name, cond, extra=""):
    global ok, fail
    ok += 1 if cond else 0
    fail += 0 if cond else 1
    print(("OK  " if cond else "FAIL ") + name, extra)


def login(sid):
    r = c.post("/api/auth/login", json={"student_id": sid})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


admin = login("2025001")

# 晚会（今天全天，GALA01 有效）里找一位已签到的志愿者和一位未签到的
acts = c.get("/api/staff/activities", headers=admin).json()
gala = next(a for a in acts if "晚会" in a["title"])
gid = gala["id"]
roster = c.get(f"/api/staff/groups/{c.get(f'/api/activities/{gid}', headers=admin).json()['groups'][0]['id']}/roster",
               headers=admin).json()
checked = [x for x in roster["confirmed"] if x["checked_in"]]
not_checked = [x for x in roster["confirmed"] if not x["checked_in"]]
assert checked and not_checked, "种子数据缺签到/未签到样本"
checked_sid = checked[0]["user"]["student_id"]
not_checked_sid = not_checked[0]["user"]["student_id"]

# 1) 学生二次扫码 = 签退
# 扫码受任务有效窗限制，而晚会场次可能在深夜后过期——
# 测试自建一个宽时限任务（valid_to = 现在+2小时），与时间解耦
from datetime import datetime, timedelta
wid = next(t["id"] for t in
           c.get(f"/api/staff/activities/{gid}/checkin-tasks",
                 headers=admin).json() if t["token"] == "GALA01")
r = c.post(f"/api/staff/groups/{c.get(f'/api/activities/{gid}', headers=admin).json()['groups'][0]['id']}/checkin-tasks",
           json={"mode": "group",
                 "valid_from": (datetime.now() - timedelta(hours=1)).isoformat(),
                 "valid_to": (datetime.now() + timedelta(hours=2)).isoformat()},
           headers=admin)
scan_task = r.json()["token"]
stu_h = login(checked_sid)
r = c.post(f"/api/checkin/task/{scan_task}", headers=stu_h)
step("二次扫码=签退", r.status_code == 200 and r.json()["action"] == "checkout",
     r.text[:80])
r = c.post(f"/api/checkin/task/{scan_task}", headers=stu_h)
step("三次扫码被拒", r.status_code == 409, str(r.status_code))

# 2) 批量签到（未签到者）+ 批量签退 + 取消签退 + 取消签到
staff_batch = f"/api/staff/checkin-tasks/{next(t['id'] for t in c.get(f'/api/staff/activities/{gid}/checkin-tasks', headers=admin).json() if t['token'] == 'GALA01')}/batch"
r = c.post(staff_batch, json={"action": "checkin",
                              "student_ids": [not_checked_sid]}, headers=admin)
step("批量手动签到", r.json()["done"] == 1, r.text[:80])
r = c.post(staff_batch, json={"action": "checkout",
                              "student_ids": [not_checked_sid, checked_sid]},
           headers=admin)
step("批量签退（含已签退跳过）", r.json()["done"] == 1 and len(r.json()["skipped"]) == 1,
     str(r.json()))
r = c.post(staff_batch, json={"action": "cancel_checkout",
                              "student_ids": [not_checked_sid]}, headers=admin)
step("取消签退", r.json()["done"] == 1)
r = c.post(staff_batch, json={"action": "cancel_checkin",
                              "student_ids": [not_checked_sid]}, headers=admin)
step("取消签到", r.json()["done"] == 1)
board = c.get(f"/api/staff/checkin-tasks/{staff_batch.split('/')[-2]}/board",
              headers=admin).json()
step("取消后记录数回落", board["checked"] == 7, f"checked={board['checked']}")

# 3) 时长计算：0.5 向上取整 + 自动兜底
# 给一位志愿者手动签到（checkin_at=now），晚会场次结束=今天23:30 → 未签退且未超宽限 → pending
r = c.post(staff_batch, json={"action": "checkin",
                              "student_ids": [not_checked_sid]}, headers=admin)
hp = c.get(f"/api/staff/activities/{gid}/hours", headers=admin).json()
item = next((x for x in hp["items"] if x["user"]["student_id"] == not_checked_sid), None)
step("宽限期内未签退→pending", item is not None and item["pending"], str(item)[:100] if item else "none")

# 已签退者：时长 = ceil((checkout-checkin)/0.5)
item2 = next((x for x in hp["items"] if x["user"]["student_id"] == checked_sid), None)
step("签退者时长为0.5倍数", item2 is not None and item2["hours"] % 0.5 == 0,
     f"h={item2['hours'] if item2 else None}")

# 4) 手动添加时长 + 删除
r = c.post(f"/api/staff/activities/{gid}/hours/manual",
           json={"items": [{"student_id": not_checked_sid, "hours": 1.5,
                            "note": "场外支援"}]}, headers=admin)
step("手动添加时长", r.status_code == 200, r.text[:80])
hp = c.get(f"/api/staff/activities/{gid}/hours", headers=admin).json()
step("手动时长出现在预览", len(hp["manual"]) == 1 and hp["manual"][0]["hours"] == 1.5)
mid = hp["manual"][0]["id"]
r = c.delete(f"/api/staff/activities/{gid}/hours/manual/{mid}", headers=admin)
step("删除手动时长", r.status_code == 200)

# 5) 学生落地页状态字段（用宽时限任务，避免深夜场次过期干扰）
stu_h = login(not_checked_sid)
info = c.get(f"/api/checkin/task/{scan_task}", headers=stu_h).json()
step("落地页状态字段", "checked_in" in info and "checkout_deadline" in info)

print(f"\nPASS {ok}  FAIL {fail}")
sys.exit(1 if fail else 0)
