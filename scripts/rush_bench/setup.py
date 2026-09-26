"""压测数据：建一个 Q 名额 + W 候补的场次（岗位名「岗」），和 N 个资料齐全的学生。

先跑 seed.main() 种演示数据（要借它的管理员账号），结果写到 /tmp/b.json 给 client.py。
"""
import os, sys, json
from datetime import timedelta
sys.path.insert(0, "/app")
from backend import seed
from backend.database import SessionLocal
from backend.models import Activity, ActivityManager, Position, Shift, User, now, uid
from backend.seed import fake_id_card
N = int(sys.argv[1]); QUOTA = int(sys.argv[2]); WL = int(sys.argv[3])
seed.main()
db = SessionLocal()
admin = db.query(User).filter(User.student_id == "2025001").one()
act = Activity(id=uid("act"), title="压测活动", intro="x", location="x",
               contacts=[{"name": "x", "phone": "13900000000"}], contact_group_qr="x",
               status="published", reg_start=now() - timedelta(days=1),
               reg_end=now() + timedelta(days=30), created_by=admin.id)
db.add(act)
db.add(ActivityManager(id=uid("mgr"), activity_id=act.id, user_id=admin.id, added_by=admin.id))
g = Shift(id=uid("g"), activity_id=act.id, name="场", start_time=now() + timedelta(days=10),
          end_time=now() + timedelta(days=10, hours=3), mode="pick", sort=0)
db.add(g); db.flush()
POSITION_TEXT = {"location": "北门", "service_task": "无特殊要求", "training_note": "x"}
p = Position(id=uid("p"), shift_id=g.id, name="岗", quota=QUOTA, waitlist_quota=WL, sort=0, **POSITION_TEXT)
db.add(p)
sids = []
for n in range(N):
    sid = f"8{n:06d}"
    db.add(User(id=uid("u"), student_id=sid, name=f"压测{n}", gender="男", college="明理书院",
                grade="2024", campus="中关村校区", phone=f"138{n:08d}", email=f"{sid}@t.l",
                wechat=f"wx{sid}", id_card=fake_id_card("20050101", n + 5000),
                user_type="student", roles=["student"]))
    sids.append(sid)
db.commit()
json.dump({"act": act.id, "g": g.id, "p": p.id, "sids": sids}, open("/tmp/b.json", "w"))
print("setup ok", N, QUOTA, WL)
