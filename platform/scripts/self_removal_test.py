import sys
sys.path.insert(0, r"F:\RUCVA_system\platform")
from fastapi.testclient import TestClient
from backend.main import app

c = TestClient(app)
r = c.post("/api/auth/login", json={"student_id": "2025001"})
h = {"Authorization": "Bearer " + r.json()["token"]}
acts = c.get("/api/staff/activities", headers=h).json()
sport = next(a for a in acts if a["type"] == "C")
mgrs = c.get(f"/api/staff/activities/{sport['id']}/managers", headers=h).json()
me = next(m for m in mgrs if m["student_id"] == "2025001")
r2 = c.delete(f"/api/staff/activities/{sport['id']}/managers/{me['id']}", headers=h)
print("self-removal:", r2.status_code, r2.json().get("detail", "")[:50])
assert r2.status_code == 400
print("SELF-REMOVAL BLOCKED ✓")
