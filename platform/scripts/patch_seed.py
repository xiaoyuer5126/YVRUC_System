# 一次性补丁：seed.py 增加用户类型/身份 + 活动管理者
import pathlib
import re

p = pathlib.Path(r"F:\RUCVA_system\platform\backend\seed.py")
src = p.read_text(encoding="utf-8")

pattern = re.compile(
    r'        staff_specs = \[.*?wang = users\["2025001"\]\n', re.S)
replacement = '''        # 用户类型（老师/学生 二选一） + 身份（可多个，可切换）
        staff_specs = [
            # 学号, 姓名, 类型, 身份
            ("2025001", "王砚秋", "teacher", ["admin", "teacher"]),   # 管理员：见全部
            ("2025002", "李慕清", "teacher", ["teacher"]),            # 老师：仅自己管理的活动
            ("2025003", "张远航", "student", ["qingxie", "student"]), # 青协（学生类型）：可管活动+用户与信用
        ]
        users: dict[str, User] = {}
        for sid, name, utype, roles in staff_specs:
            u = User(id=uid("u"), student_id=sid, name=name,
                     gender="女" if name[1] in "砚慕" else "男",
                     college="校团委" if "admin" in roles else "校青年志愿者协会",
                     phone=f"139{sid[-4:]}0000", email=f"{sid}@ruc.edu.cn",
                     user_type=utype, roles=roles)
            db.add(u)
            users[sid] = u
        wang = users["2025001"]
        limu = users["2025002"]
        zhang = users["2025003"]
'''
src, n = pattern.subn(replacement, src)
print("staff block replaced:", n)

old_student = """                     id_card=f"110101{grade}{'05' if GENDERS[i % 2] == '女' else '11'}"
                             f"{(i % 27) + 1:02d}{random.randint(1000, 9999)}X")
            db.add(u)"""
new_student = """                     id_card=f"110101{grade}{'05' if GENDERS[i % 2] == '女' else '11'}"
                             f"{(i % 27) + 1:02d}{random.randint(1000, 9999)}X",
                     user_type="student", roles=["student"])
            db.add(u)"""
assert old_student in src, "student block not found"
src = src.replace(old_student, new_student)

# ---- 活动管理者分配 ----
# sport: 王砚秋(创建者) + 李慕清；book: 王砚秋 + 张远航；
# fair: 张远航(创建者)；gala: 张远航(创建者)；elder: 李慕清(创建者)
src = src.replace('created_by=wang.id, created_at=dt(days=-4))',
                  'created_by=wang.id, created_at=dt(days=-4))')
src = src.replace('created_by=wang.id, created_at=dt(days=-3))\n        db.add(book)',
                  'created_by=wang.id, created_at=dt(days=-3))\n        db.add(book)')
# fair / gala 改为张远航创建
src = src.replace('status="published", reg_start=dt(days=-1),\n                        reg_end=dt(days=4), created_by=wang.id,\n                        created_at=dt(days=-2))',
                  'status="published", reg_start=dt(days=-1),\n                        reg_end=dt(days=4), created_by=zhang.id,\n                        created_at=dt(days=-2))')
src = src.replace('status="closed", reg_start=dt(days=-6), reg_end=dt(days=-2),\n                        created_by=wang.id, created_at=dt(days=-7))',
                  'status="closed", reg_start=dt(days=-6), reg_end=dt(days=-2),\n                        created_by=zhang.id, created_at=dt(days=-7))')
# elder 改为李慕清创建
src = src.replace('status="finished", reg_start=dt(days=-20),\n                         reg_end=dt(days=-12), created_by=wang.id,\n                         created_at=dt(days=-22))',
                  'status="finished", reg_start=dt(days=-20),\n                         reg_end=dt(days=-12), created_by=limu.id,\n                         created_at=dt(days=-22))')

# 在审计留痕前插入管理者数据
anchor = "        db.commit()\n\n        # ---------- 审计留痕 ----------"
managers_block = """        db.commit()

        # ---------- 活动管理者（创建者自动成为管理者，另配多人共管） ----------
        from .models import ActivityManager
        acts_by_title = {a.title: a for a in db.query(Activity).all()}
        mgr_plan = [
            ("校运动会开幕式志愿服务", [wang, limu]),
            ("图书馆图书整理周·周六专场", [wang, zhang]),
            ("校园开放日·招生咨询志愿讲解", [zhang]),
            ("校庆文艺晚会·当日保障", [zhang]),
            ("社区敬老院陪伴服务（秋）", [limu]),
        ]
        for title, mgrs in mgr_plan:
            act = acts_by_title[title]
            for m in mgrs:
                db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                                       user_id=m.id, added_by=act.created_by))
        db.commit()

        # ---------- 审计留痕 ----------"""
assert anchor in src, "audit anchor not found"
src = src.replace(anchor, managers_block)

p.write_text(src, encoding="utf-8")
print("seed patched OK")
