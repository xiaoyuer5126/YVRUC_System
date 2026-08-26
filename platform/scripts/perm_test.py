# 权限体系集成测试
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
    return {"Authorization": "Bearer " + r.json()["token"]}


admin = login("2025001")   # 王砚秋 管理员+老师
limu = login("2025002")    # 李慕清 老师
zhang = login("2025003")   # 张远航 学生类型·青协+学生身份
stu = login("202200100")   # 陈雨桐 纯学生

acts_admin = c.get("/api/staff/activities", headers=admin).json()
acts_limu = c.get("/api/staff/activities", headers=limu).json()
acts_zhang = c.get("/api/staff/activities", headers=zhang).json()
step("管理员见全部活动", len(acts_admin) == 5, f"n={len(acts_admin)}")
step("老师只见自己管理的", {a["title"] for a in acts_limu} ==
     {"校运动会开幕式志愿服务", "社区敬老院陪伴服务（秋）"}, str(len(acts_limu)))
step("青协只见自己管理的", {a["title"] for a in acts_zhang} ==
     {"图书馆图书整理周·周六专场", "校园开放日·招生咨询志愿讲解", "校庆文艺晚会·当日保障"},
     str(len(acts_zhang)))

step("数据总览仅管理员", c.get("/api/staff/overview", headers=admin).status_code == 200
     and c.get("/api/staff/overview", headers=limu).status_code == 403)
step("用户与信用 管理员/青协", c.get("/api/staff/users", headers=admin).status_code == 200
     and c.get("/api/staff/users", headers=zhang).status_code == 200
     and c.get("/api/staff/users", headers=limu).status_code == 403)
step("集成设置仅管理员", c.get("/api/staff/integrations", headers=admin).status_code == 200
     and c.get("/api/staff/integrations", headers=zhang).status_code == 403)
step("学生不能进工作台", c.get("/api/staff/activities", headers=stu).status_code == 403)

book = next(a for a in acts_admin if "图书" in a["title"])
r = c.patch(f"/api/staff/activities/{book['id']}", json={"intro": "x"}, headers=limu)
step("非管理者改活动被拒", r.status_code == 403, str(r.status_code))
sport = next(a for a in acts_admin if "运动会" in a["title"])
r = c.patch(f"/api/staff/activities/{sport['id']}",
            json={"location": "世纪馆田径场"}, headers=limu)
step("管理者可改自己活动", r.status_code == 200, str(r.status_code))

mgrs = c.get(f"/api/staff/activities/{sport['id']}/managers", headers=admin).json()
step("管理者列表含创建者标记",
     any(m["is_creator"] for m in mgrs) and len(mgrs) == 2, str(len(mgrs)))
r = c.post(f"/api/staff/activities/{sport['id']}/managers",
           json={"student_id": "202200100"}, headers=admin)
step("纯学生身份不能被设为管理者", r.status_code == 400, str(r.status_code))
r = c.post(f"/api/staff/activities/{sport['id']}/managers",
           json={"student_id": "2025003"}, headers=limu)
step("管理者可添加管理者(青协身份)", r.status_code == 200, r.text[:80])
mgrs = c.get(f"/api/staff/activities/{sport['id']}/managers", headers=admin).json()
creator = next(m for m in mgrs if m["is_creator"])
r = c.delete(f"/api/staff/activities/{sport['id']}/managers/{creator['id']}",
             headers=limu)
step("创建者不可被移除", r.status_code == 409, str(r.status_code))
zhang_id = next(m["id"] for m in mgrs
                if not m["is_creator"] and m["student_id"] == "2025003")
r = c.delete(f"/api/staff/activities/{sport['id']}/managers/{zhang_id}",
             headers=limu)
step("管理者可移除其他管理者", r.status_code == 200, str(r.status_code))

audit_limu = c.get("/api/staff/audit-logs", headers=limu).json()
step("审计日志按管理范围过滤", audit_limu["total"] > 0,
     f"total={audit_limu['total']}")

print(f"\nPASS {ok}  FAIL {fail}")
sys.exit(1 if fail else 0)
