"""演示数据种子。

覆盖设计方案五阶段各一个活动，日期全部相对当前时间生成，
保证任何一天跑起来都是"活的"。岗位模式三种都有演示：
  · 校运动会开幕式（C类·报名中·直接定二级）——岗位满员→候补、
    候补转正确认窗口（2小时倒计时）
  · 图书馆图书整理周（B类·报名中·二级待定）——调配期主角，
    进后台建岗位后一键智能排班
  · 校园开放日招生咨询（A类·报名中·仅一级）——简单活动无需岗位
  · 校庆文艺晚会（D类·已截止·签到进行中）——统一+分岗位签到码、实时大屏
  · 社区敬老院陪伴服务（A类·已结束·仅一级）——时长已确认并回传数智人大

运行：python -m backend.seed   （重复执行会先清空重建）
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy import text

from .database import SessionLocal, engine, init_db
from .qrcodes import qr_svg
from .models import (Activity, ActivityManager, AuditLog, CheckinRecord,
                     CheckinTask, CreditLog, Enrollment, HoursRecord,
                     Notification, Shift, Position, User, uid)

random.seed(42)

NAMES = [
    "陈雨桐", "林子昂", "王思远", "赵一诺", "刘畅", "张若谷", "李昊然",
    "周芷若", "吴一凡", "郑晓岚", "冯启航", "褚天翼", "卫来", "蒋星河",
    "沈亦臻", "韩梅梅", "杨光", "朱雀", "秦朗", "许知远", "何欢",
    "吕行知", "施雨泽", "张启铭", "孔维真", "曹清扬", "严望舒", "华清越",
    "金希文", "魏来仪", "陶然", "姜暖", "戚少衡", "谢云舒", "邹阳",
    "喻言", "柏舟", "水静姝", "窦文昊", "章涵", "云以梦", "苏澜",
    "潘阳", "葛天", "奚望", "范柳青", "彭湃", "鲁川", "温言", "廉思",
]

GENDERS = ["女", "男"]


ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
ID_CHECK = "10X98765432"


def fake_id_card(birth: str, seq: int) -> str:
    """生成校验位正确的 18 位身份证号（routers/me.py 会校验末位）。"""
    body = f"110101{birth}{seq % 1000:03d}"
    total = sum(int(c) * w for c, w in zip(body, ID_WEIGHTS))
    return body + ID_CHECK[total % 11]



def demo_qr(label: str) -> str:
    """演示用答疑群二维码（内联 SVG，不依赖外部文件）。

    创建活动时二维码是必填项，种子数据不给的话活动详情页就是一片空白，
    看起来像功能没做。
    """
    import base64
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160">'
           f'<rect width="160" height="160" fill="#fff"/>'
           f'{"".join(qr_svg(label).split(chr(10))[1:-1])}'
           f'</svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(
        svg.encode("utf-8")).decode()


def dt(days=0, hours=0, minutes=0, base=None):
    """相对时间工具：支持小数小时（如 10.5 → 10:30）。"""
    b = base or datetime.now().replace(second=0, microsecond=0)
    return b + timedelta(days=days, hours=float(hours), minutes=minutes)


def main() -> None:
    # 整个 schema 重建，而不是逐表 delete、也不是 metadata.drop_all：
    # 模型改了名字时，旧表还在库里且不在 metadata 里，drop_all 会因为
    # 外键依赖失败；只删数据则会留下旧结构，插入时报「column 不存在」。
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    init_db()
    db = SessionLocal()
    try:

        t0 = datetime.now().replace(minute=0, second=0, microsecond=0)

        # ---------- 用户 ----------
        # 用户类型（老师/学生 二选一） + 身份（可多个，可切换）
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
                     wechat=f"wx_{sid}",
                     # 学生类型（如青协同学）同样要求手机号/微信/证件号齐全
                     id_card="" if utype == "teacher" else "110101200512250019",
                     user_type=utype, roles=roles)
            db.add(u)
            users[sid] = u
        wang = users["2025001"]
        limu = users["2025002"]
        zhang = users["2025003"]

        colleges = ["明理书院", "新闻学院", "法学院", "财政金融学院", "经济学院",
                    "信息学院", "外国语学院", "马克思主义学院", "社会学院",
                    "统计学院"]
        students: list[User] = []
        for i, nm in enumerate(NAMES):
            college = colleges[i % len(colleges)]
            grade = ["2022", "2023", "2023", "2024"][i % 4]
            u = User(id=uid("u"), student_id=f"{grade}{i // len(colleges):02d}"
                                             f"{100 + i:03d}",
                     name=nm, gender=GENDERS[i % 2], college=college,
                     grade=grade,
                     phone="138" + str(random.randint(10000000, 99999999)),
                     email=f"ruc{grade}_{i}@163.com",
                     wechat=f"wx{i}",
                     id_card=fake_id_card(
                         f"{grade}{'05' if GENDERS[i % 2] == '女' else '11'}"
                         f"{(i % 27) + 1:02d}", i),
                     user_type="student", roles=["student"])
            db.add(u)
            students.append(u)
        db.flush()

        # 演示「资料未完善」：陶然缺手机号/微信号 → 报名被拦截并弹出完善表单
        taoran = students[30]
        taoran.phone = ""
        taoran.wechat = ""

        # 特殊状态学生（信用冻结 / 限制准入 / 放弃候补×2）
        frozen = students[11]
        frozen.credit_score = 55
        frozen.credit_frozen_until = dt(days=20)
        db.add(CreditLog(id=uid("crd"), user_id=frozen.id, delta=-5,
                         after_score=95, reason="临近退选：距「校园开放日」开始不足24小时",
                         created_at=dt(days=-21)))
        db.add(CreditLog(id=uid("crd"), user_id=frozen.id, delta=-40,
                         after_score=55, reason="C类活动确认后无故缺席且无法联系",
                         operator_id=wang.id, created_at=dt(days=-19)))

        restricted = students[23]
        restricted.restricted_types = ["C", "D"]
        restricted.restricted_until = dt(days=25)
        db.add(CreditLog(id=uid("crd"), user_id=restricted.id, delta=-10,
                         after_score=90, reason="大型校级活动临场消极志愿（限制准入C/D至%s）"
                         % (dt(days=25).strftime("%Y-%m-%d")),
                         operator_id=wang.id, created_at=dt(days=-15)))

        students[35].abandon_count = 2
        db.commit()

        # ---------- 岗位助手（无时间：职责/地点/名额/要求） ----------
        # 服务地点/要求原文/培训内容都是必填，演示数据也要满足——
        # 否则后台一改岗位就被自己的校验拦下
        def stn(shift, name, loc, quota, tags=None,
                note="服从现场安排，提前 15 分钟到岗",
                train="到岗后由负责人统一讲解岗位职责与应急流程",
                leader=False, waitlist=3):
            p = Position(id=uid("p"), shift_id=shift.id, name=name,
                         location=loc, quota=quota, waitlist_quota=waitlist,
                         requirement_tags=tags or {}, requirement_note=note,
                         training_note=train, needs_leader=leader)
            db.add(p)
            return p

        def enr(user, act, group, days_ago, status="confirmed", position=None,
                assigned=True, allow_reassign=None):
            e = Enrollment(id=uid("enr"), activity_id=act.id,
                           shift_id=group.id, user_id=user.id,
                           position_id=position.id if position else None,
                           status=status, source="self",
                           # 约四分之一的人不接受调剂，分配面板上能看到提示
                           allow_reassign=(True if status != "confirmed"
                                           else (random.random() > 0.25
                                                 if allow_reassign is None
                                                 else allow_reassign)),
                           leader_willing=random.random() > 0.6,
                           joined_at=dt(days=-days_ago,
                                        minutes=random.randint(-50, 50)))
            if status == "confirmed" and assigned and position:
                e.assigned_at = e.joined_at
            db.add(e)
            return e

        # ---------- 活动1：运动会（报名中·直接定二级） ----------
        sport = Activity(id=uid("act"), title="校运动会开幕式志愿服务",
                         type="C", cover_hue=350,
                         intro="全校秋季运动会开幕式需要大量志愿者支持，涉及引导、检录、礼仪、医疗辅助等岗位。"
                               "请仔细核对时间后报名；开始前24小时内退选将扣除信用分。",
                         location="世纪馆田径场",
                         contact_info="青协项目部 李慕清 138xxxx0002（微信同）",
                         contacts=[{"name": "李慕清", "phone": "138xxxx0002"},
                                   {"name": "王砚秋", "phone": "13950010000"}],
                         qa_group_qr=demo_qr("RUCVA-QA-GROUP"),
                         status="published",
                         reg_start=dt(days=-3), reg_end=dt(days=2),
                         created_by=wang.id, created_at=dt(days=-4))
        db.add(sport)

        m1 = Shift(id=uid("g"), activity_id=sport.id, name="上午场",
                           start_time=dt(days=3, base=t0.replace(hour=8)),
                           end_time=dt(days=3, base=t0.replace(hour=12)),
                           mode="pick", sort=0)
        m2 = Shift(id=uid("g"), activity_id=sport.id, name="下午场",
                           start_time=dt(days=3, base=t0.replace(hour=14)),
                           end_time=dt(days=3, base=t0.replace(hour=17, minute=30)),
                           mode="pick", sort=1)
        db.add_all([m1, m2])
        db.flush()

        s_gate = stn(m1, "东门引导", "校本部东门", 8, {},
                     "熟悉校园路线，口齿清晰", "提前30分钟到东门集合培训")
        s_check = stn(m1, "检录服务", "田径场检录处", 7, {},
                      "负责运动员检录与号码布发放")
        s_etiq = stn(m1, "颁奖礼仪", "主席台前区", 4,
                     {"性别": "女"}, "颁奖环节礼仪志愿，着装统一发放",
                     "赛前一周 19:00 线上培训会议")
        s_med = stn(m1, "医疗辅助", "场地东侧医疗点", 4, {},
                    "协助校医搬运物资、维持秩序", "需参加基础急救常识培训")
        o_order = stn(m2, "秩序维护", "看台入口", 6)
        o_gear = stn(m2, "器材协助", "器材室", 5)
        o_final = stn(m2, "终点服务", "终点线", 5)
        db.flush()

        pool = students[:]
        used: list[User] = []

        def take(pred=None):
            for u in pool:
                if u in used:
                    continue
                if pred and not pred(u):
                    continue
                used.append(u)
                return u
            raise RuntimeError("演示人数不够了")

        # 上午场：东门满8 / 检录满7 / 礼仪3（女）/ 医疗2 = 20人确认
        for _ in range(8):
            enr(take(), sport, m1, 3, position=s_gate)
        for _ in range(7):
            enr(take(), sport, m1, 2, position=s_check)
        for _ in range(3):
            enr(take(lambda u: u.gender == "女"), sport, m1, 2, position=s_etiq)
        for _ in range(2):
            enr(take(lambda u: u.gender == "女"), sport, m1, 2, position=s_med)
        # 一名候补 + 一名转正待确认（都选了已满员的东门引导）
        wl_user = take()
        enr(wl_user, sport, m1, 1, status="waitlisted", position=s_gate,
            assigned=False)
        pc_user = take(lambda u: u.gender == "女")
        e_pc = enr(pc_user, sport, m1, 1, status="pending_confirm",
                   position=s_gate, assigned=False)
        e_pc.promoted_at = dt(hours=-1)
        e_pc.confirm_deadline = dt(hours=1)   # 演示：还有1小时确认窗口

        # 一名临近退选：扣分且名额被扣留不释放（roster 里显示为占位）
        late_user = take()
        late_enr = enr(late_user, sport, m1, 1, status="cancelled",
                       position=s_check, assigned=False)
        late_enr.cancel_type = "late"
        late_enr.seat_held = True
        late_user.credit_score = 95
        db.add(CreditLog(id=uid("crd"), user_id=late_user.id, delta=-5,
                         after_score=95,
                         reason="临近退选：距「校运动会开幕式志愿服务·上午场」开始不足24小时",
                         created_at=dt(hours=-5)))

        # 下午场 13人
        for _ in range(6):
            enr(take(), sport, m2, 2, position=o_order)
        for _ in range(4):
            enr(take(), sport, m2, 2, position=o_gear)
        for _ in range(3):
            enr(take(), sport, m2, 1, position=o_final)

        # ---------- 活动2：图书整理周（报名中·二级待定） ----------
        book = Activity(id=uid("act"), title="图书馆图书整理周·周六专场",
                        type="B", cover_hue=210,
                        intro="图书馆馆藏调整需要大批志愿者参与图书下架、搬运与重新上架。"
                              "具体分工将在报名截止后由青协统一排班公布。",
                        location="图书馆（明德楼）",
                        contact_info="青协项目部 lib-vol@ruc.edu.cn",
                        contacts=[{"name": "张远航", "phone": "13950030000"}],
                        qa_group_qr=demo_qr("RUCVA-QA-GROUP"),
                        status="published",
                        reg_start=dt(days=-2), reg_end=dt(days=3),
                        created_by=wang.id, created_at=dt(days=-3))
        db.add(book)
        b1 = Shift(id=uid("g"), activity_id=book.id, name="周六全天场",
                           start_time=dt(days=5, base=t0.replace(hour=9)),
                           end_time=dt(days=5, base=t0.replace(hour=17)),
                           capacity=20, mode="assign",
                           waitlist_quota=3,
                           note="具体岗位（分拣/搬运/上架）报名截止后由排班程序统一分配",
                           sort=0)
        db.add(b1)
        db.flush()
        frozen_id = frozen.id
        cnt = 0
        for u in students:
            if cnt >= 18 or u.id == frozen_id:
                continue
            db.add(Enrollment(id=uid("enr"), activity_id=book.id,
                              shift_id=b1.id, user_id=u.id, status="confirmed",
                              source="self",
                              allow_reassign=random.random() > 0.25,
                              joined_at=dt(days=-2, minutes=cnt * 7)))
            cnt += 1

        # ---------- 活动3：招生咨询会（报名中·仅一级） ----------
        fair = Activity(id=uid("act"), title="校园开放日·招生咨询志愿讲解",
                        type="A", cover_hue=150,
                        intro="面向来访考生家长的咨询引导志愿服务。岗位简单，无需分岗位，"
                              "报名即锁定时段。",
                        location="明德广场", contact_info="招办协同 张远航",
                        contacts=[{"name": "张远航", "phone": "13950030000"}],
                        qa_group_qr=demo_qr("RUCVA-QA-GROUP"),
                        status="published", reg_start=dt(days=-1),
                        reg_end=dt(days=4), created_by=zhang.id,
                        created_at=dt(days=-2))
        db.add(fair)
        f1 = Shift(id=uid("g"), activity_id=fair.id, name="下午讲解场",
                           start_time=dt(days=5, base=t0.replace(hour=13)),
                           end_time=dt(days=5, base=t0.replace(hour=17)),
                           mode="pick", sort=0)
        db.add(f1)
        db.flush()
        f1_pos = stn(f1, "校园讲解", "明德主楼北门", 10,
                     note="带队讲解 2 条校园线路，需提前熟悉讲解词",
                     train="讲解词与线路图见岗位材料", leader=True)
        db.flush()
        # 3 人同时报了图书整理周（时段重叠素材）；其余 4 人正常
        book_rows = db.query(Enrollment).filter(Enrollment.shift_id == b1.id).all()
        book_ids = {e.user_id for e in book_rows}
        book_members = list(book_ids)[:3]
        for uid_ in book_members:
            u = db.get(User, uid_)
            db.add(Enrollment(id=uid("enr"), activity_id=fair.id,
                              shift_id=f1.id, user_id=u.id, status="confirmed",
                              position_id=f1_pos.id, source="self",
                              assigned_at=dt(days=-1),
                              joined_at=dt(days=-1)))
        rest = [u for u in students
                if u.id not in book_ids and u.id != frozen_id][:4]
        for u in rest:
            db.add(Enrollment(id=uid("enr"), activity_id=fair.id,
                              shift_id=f1.id, user_id=u.id, status="confirmed",
                              position_id=f1_pos.id, source="self",
                              assigned_at=dt(days=-1),
                              joined_at=dt(days=-1)))

        # ---------- 活动4：校庆晚会当日保障（已截止·签到进行中·今天全天） ----------
        gala = Activity(id=uid("act"), title="校庆文艺晚会·当日保障",
                        type="D", cover_hue=280,
                        intro="校庆晚会当天全流程保障，已截止报名。请按分配岗位准时到岗并扫码签到。",
                        location="明德堂",
                        contact_info="青协办公室 张远航",
                        contacts=[{"name": "王砚秋", "phone": "13950010000"},
                                  {"name": "张远航", "phone": "13950030000"}],
                        qa_group_qr=demo_qr("RUCVA-QA-GROUP"),
                        status="closed", reg_start=dt(days=-6), reg_end=dt(days=-2),
                        created_by=zhang.id, created_at=dt(days=-7))
        db.add(gala)
        c1 = Shift(id=uid("g"), activity_id=gala.id, name="晚会当日班",
                           start_time=dt(base=t0.replace(hour=8, minute=0)),
                           end_time=dt(base=t0.replace(hour=23, minute=30)),
                           mode="pick", sort=0)
        db.add(c1)
        db.flush()
        c_s1 = stn(c1, "嘉宾引导", "明德堂前厅", 4)
        c_s2 = stn(c1, "后台秩序", "后台通道", 4)
        c_s3 = stn(c1, "观众引导", "观众席入口", 4)
        db.flush()
        gala_users = students[38:50]
        assigned_map = [c_s1] * 4 + [c_s2] * 4 + [c_s3] * 4
        task_gala = CheckinTask(id=uid("tsk"), activity_id=gala.id, shift_id=c1.id,
                                mode="shift", token="GALA01",
                                valid_from=c1.start_time, valid_to=c1.end_time,
                                active=True, created_by=wang.id)
        db.add(task_gala)
        db.flush()
        for i, (u, st_) in enumerate(zip(gala_users, assigned_map)):
            joined = dt(days=-5, minutes=i * 9)
            db.add(Enrollment(id=uid("enr"), activity_id=gala.id,
                              shift_id=c1.id, user_id=u.id, status="confirmed",
                              position_id=st_.id, assigned_at=joined,
                              joined_at=joined, source="self"))
            if i < 7:   # 前7人已到岗
                ck_at = max(c1.start_time, t0 - timedelta(minutes=35 + i * 12))
                db.add(CheckinRecord(id=uid("ckr"), task_id=task_gala.id,
                                     activity_id=gala.id, shift_id=c1.id,
                                     position_id=st_.id, user_id=u.id,
                                     method="scan", checkin_at=ck_at))
        task_back = CheckinTask(id=uid("tsk"), activity_id=gala.id,
                                shift_id=c1.id, position_id=c_s2.id,
                                mode="position", token="BACK02",
                                valid_from=c1.start_time,
                                valid_to=c1.end_time, active=True,
                                created_by=wang.id)
        db.add(task_back)

        # ---------- 活动5：敬老院（已结束·仅一级·时长已回传） ----------
        elder = Activity(id=uid("act"), title="社区敬老院陪伴服务（秋）",
                         type="A", cover_hue=30,
                         intro="每周定期前往附近社区敬老院陪伴老人，聊天读报、组织小活动。"
                               "岗位简单，仅按场次报名。",
                         location="海淀街道敬老院",
                         contact_info="青协项目部 李慕清",
                         contacts=[{"name": "李慕清", "phone": "138xxxx0002"}],
                         qa_group_qr=demo_qr("RUCVA-QA-GROUP"),
                         status="finished", reg_start=dt(days=-20),
                         reg_end=dt(days=-12), created_by=limu.id,
                         created_at=dt(days=-22))
        db.add(elder)
        e1 = Shift(id=uid("g"), activity_id=elder.id, name="上周六场",
                           start_time=dt(days=-7, base=t0.replace(hour=9)),
                           end_time=dt(days=-7, base=t0.replace(hour=12)),
                           mode="pick", sort=0)
        db.add(e1)
        db.flush()
        e1_pos = stn(e1, "陪伴服务", "海淀区敬老院", 10)
        db.flush()
        elder_task = CheckinTask(id=uid("tsk"), activity_id=elder.id,
                                 shift_id=e1.id, mode="shift", token="ELDER9",
                                 valid_from=e1.start_time, valid_to=e1.end_time,
                                 active=False, created_by=wang.id)
        db.add(elder_task)
        db.flush()
        elder_pool = students[20:28]
        for i, u in enumerate(elder_pool):
            joined = dt(days=-12, minutes=i * 11)
            db.add(Enrollment(id=uid("enr"), activity_id=elder.id,
                              position_id=e1_pos.id,
                              shift_id=e1.id, user_id=u.id, status="confirmed",
                              joined_at=joined, assigned_at=joined))
            db.add(CheckinRecord(id=uid("ckr"), task_id=elder_task.id,
                                 activity_id=elder.id, shift_id=e1.id,
                                 user_id=u.id, method="scan",
                                 checkin_at=e1.start_time + timedelta(minutes=5 + i)))
            db.add(HoursRecord(id=uid("hrs"), activity_id=elder.id,
                               user_id=u.id, hours=3.0,
                               status="uploaded", upload_ref="MOCK-ELDER-0008",
                               uploaded_at=dt(days=-5), confirmed_by=wang.id))

        db.commit()

        # ---------- 活动管理者（创建者自动成为管理者，另配多人共管） ----------
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

        # ---------- 审计留痕 ----------
        def aud(action, target_type, target_id, label, act_id, summary,
                when, changes=None, visible=True, actor=None):
            db.add(AuditLog(id=uid("aud"), actor_id=(actor or wang).id,
                            actor_name=(actor or wang).name, action=action,
                            target_type=target_type, target_id=target_id,
                            target_label=label, activity_id=act_id,
                            summary=summary, changes=changes or [],
                            visible=visible, created_at=when))

        aud("create", "activity", sport.id, sport.title, sport.id,
            "创建活动「校运动会开幕式志愿服务」", dt(days=-4))
        aud("publish", "activity", sport.id, sport.title, sport.id,
            "发布活动，开始报名", dt(days=-3))
        aud("update", "group", m1.id, "运动会·上午场", sport.id,
            "集合地点由「南门」变更为「东门」",
            dt(days=-1, hours=-6),
            [{"field": "note", "label": "备注",
              "old": "志愿者南门集合", "new": "志愿者东门集合"}])
        aud("update", "position", s_etiq.id, "运动会·颁奖礼仪", sport.id,
            "礼仪岗增加性别要求（女），并补充线上培训安排",
            dt(days=-2, hours=3),
            [{"field": "requirement_tags", "label": "条件要求",
              "old": "不限", "new": "性别=女"},
             {"field": "training_note", "label": "培训内容",
              "old": "", "new": "赛前一周 19:00 线上培训会议"}])
        aud("create", "activity", book.id, book.title, book.id,
            "创建活动「图书馆图书整理周·周六专场」（二级待定模式）",
            dt(days=-3))
        aud("publish", "activity", book.id, book.title, book.id,
            "发布活动，开始报名", dt(days=-2))
        aud("assign", "assignment", c1.id, "晚会·当日班", gala.id,
            "12 人完成岗位分配（其中强制分配 0 人）", dt(days=-5), visible=True,
            actor=users["2025003"])
        aud("create", "checkin", task_gala.id, "晚会签到码 GALA01", gala.id,
            "生成统一签到码 GALA01", dt(days=-1),
            actor=users["2025003"])
        aud("create", "activity", elder.id, elder.title, elder.id,
            "创建活动「社区敬老院陪伴服务（秋）」（仅场次模式）",
            dt(days=-22))
        aud("hours_upload", "hours", elder.id, elder.title, elder.id,
            "回传 8 人时长至数智人大（回执号 MOCK-ELDER-0008）", dt(days=-5))
        aud("restriction", "user", restricted.id,
            f"{restricted.name}（{restricted.student_id}）", None,
            "限制准入：不得参与C/D类活动至%s（依据《志愿者服务规范》）"
            % (dt(days=25).strftime("%Y-%m-%d")), dt(days=-15))
        db.commit()

        # ---------- 通知样例 ----------
        def notify(u, key, title, content, when, read=False):
            db.add(Notification(id=uid("ntf"), user_id=u.id, template_key=key,
                                title=title, content=content, read=read,
                                created_at=when))

        notify(pc_user, "waitlist_promoted", "候补转正待确认｜校运动会开幕式志愿服务",
               f"「校运动会开幕式志愿服务」· 上午场 有名额释放，您已按候补顺序转正！\n"
               f"请在 {e_pc.confirm_deadline:%m-%d %H:%M} 前确认，超时名额顺延。",
               dt(hours=-1))
        notify(wl_user, "waitlist_queued", "已加入候补｜校运动会开幕式志愿服务",
               "「校运动会开幕式志愿服务」· 上午场 名额已满，您已加入候补队列，当前第 2 位。",
               dt(days=-1))
        for u in students[3:6]:
            notify(u, "signup_success", "报名成功｜校运动会开幕式志愿服务",
                   "您已成功报名「校运动会开幕式志愿服务」，时段已锁定。\n"
                   "如无法参加请提前24小时退选。", dt(days=-2, hours=-3),
                   read=bool(random.random() < 0.5))
        notify(frozen, "credit_penalty", "信用分变动提醒",
               "您的信用分 -40 分（当前 55 分）。\n事由：C类活动确认后无故缺席且无法联系\n"
               "⚠️ 信用分低于60分，报名权限冻结至 %s。" %
               frozen.credit_frozen_until.strftime("%Y-%m-%d"),
               dt(days=-19))
        db.commit()
        print("SEED OK:")
        print("  用户:", db.query(User).count())
        print("  活动:", db.query(Activity).count(),
              " 场次:", db.query(Shift).count(),
              " 岗位:", db.query(Position).count())
        print("  报名:", db.query(Enrollment).count())
        print("  签到记录:", db.query(CheckinRecord).count())
        print("  时长记录:", db.query(HoursRecord).count())
        print("  审计日志:", db.query(AuditLog).count())
        print("  待确认演示账号:", pc_user.student_id, pc_user.name)
        print("  冻结演示账号:", frozen.student_id, frozen.name)
    finally:
        db.close()


if __name__ == "__main__":
    main()
