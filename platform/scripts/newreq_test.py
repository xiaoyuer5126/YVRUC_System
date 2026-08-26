# 本轮新需求抽查：资料完善拦截、候补名额上限、通知、上传
import sys
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
    d = r.json()
    return {"Authorization": "Bearer " + d["token"]}, d["user"]


admin, _ = login("2025001")
acts = c.get("/api/staff/activities", headers=admin).json()
sport = next(a for a in acts if a["type"] == "C")
detail = c.get(f"/api/activities/{sport['id']}", headers=admin).json()
m1 = detail["groups"][0]
gate = next(s for s in m1["stations"] if s["name"] == "东门引导")

# 1) 未完善资料的学生（陶然）报名被拦
tauran, u = login("202303130")
step("未完善学生 profile_complete=False", u["profile_complete"] is False,
     str(u["roles"]))
r = c.post(f"/api/activities/{sport['id']}/groups/{m1['id']}/enroll",
           json={"station_id": gate["id"]}, headers=tauran)
step("未完善报名被拦", r.status_code == 403 and "完善个人信息" in r.json()["detail"],
     r.text[:60])

# 2) 完善后可正常报名（东门满→进候补）
r = c.put("/api/me/profile",
          json={"phone": "13800001234", "wechat": "tauran_wx",
                "id_card": "110101200306120030"}, headers=tauran)
step("完善资料接口", r.status_code == 200 and r.json()["user"]["profile_complete"],
     r.text[:60])
r = c.post(f"/api/activities/{sport['id']}/groups/{m1['id']}/enroll",
           json={"station_id": gate["id"]}, headers=tauran)
step("完善后报名→候补", r.status_code == 200 and r.json()["status"] == "waitlisted",
     r.text[:80])

# 3) 候补名额上限（默认3：种子2人+陶然=3，第4人被拒）
extra, _ = login("202304145")   # 范柳青，未报名运动会
r = c.post(f"/api/activities/{sport['id']}/groups/{m1['id']}/enroll",
           json={"station_id": gate["id"]}, headers=extra)
step("候补满员被拒(上限3)", r.status_code == 409 and "候补名额已满" in r.json()["detail"],
     r.text[:60])

# 4) 通知接口：向上午场未到岗志愿者发通知
r = c.post(f"/api/staff/activities/{sport['id']}/notify",
           json={"scope": "group", "group_id": m1["id"],
                 "title": "培训通知", "content": "今晚19:00线上培训，请准时参加"},
           headers=admin)
step("向场次发通知", r.status_code == 200 and r.json()["count"] >= 15, str(r.json())[:60])

# 5) 图片上传
png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c626001000000ffff030000060005"
                    "57bfabd40000000049454e44ae426082")
r = c.post("/api/staff/upload-image", files={"file": ("qr.png", png, "image/png")},
           headers=admin)
step("二维码图片上传", r.status_code == 200 and r.json()["url"].startswith("/api/media/"),
     r.text[:80])
url = r.json()["url"]
r = c.get(url)
step("上传图片可访问", r.status_code == 200 and r.content[:4] == b"\x89PNG")

# 6) 活动详情带 contacts 与 waitlist_quota
d2 = c.get(f"/api/activities/{sport['id']}", headers=admin).json()
step("contacts 下发", len(d2["contacts"]) >= 1 and "name" in d2["contacts"][0])
step("waitlist_quota 下发", d2["groups"][0]["waitlist_quota"] == 3)

print(f"\nPASS {ok}  FAIL {fail}")
sys.exit(1 if fail else 0)
