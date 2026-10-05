"""演示数据种子。

活动流程的各个阶段、三种招募方式、两种场次模式都有演示，日期全部相对
当前时间生成，保证任何一天跑起来都是"活的"：
  · 校运动会开幕式（即选即得·报名中·报名即选岗位）——岗位满员→候补、
    候补转正确认窗口（2小时倒计时）
  · 图书馆图书整理周（即选即得·报名中·截止后统一分配）——报名都落在
    「待分配」，进后台建岗位后一键分配
  · 校园开放日招生咨询（即选即得·报名中）——一个场次一个岗位的简单活动
  · 校庆文艺晚会（已截止·签到进行中）——统一+分岗位签到码、实时大屏
  · 社区敬老院陪伴服务（已完成）——时长已核准并锁定
  · 新生迎新周（报名后筛选·报名中）——报名问卷与意向场次
  · 学院读书会志愿服务（补记录·已结束）——名单已导入，等待核准时长

运行：python -m backend.seed   （重复执行会先清空重建）
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy import text

from . import config
from .audit import log as audit_log
from .database import SessionLocal, engine, init_db
from .qrcodes import qr_svg
from .models import (Activity, ActivityManager, Application, AuditLog, CheckinRecord, CheckinTask, Enrollment, FormQuestion, HoursRecord, Notification, Position, Shift, User, ViolationRecord, uid)
# 已确认的时长要连明细一起种：明细不是另写的一段文字，是这套算法从签到记录
# 里推出来的，所以直接借过来用，免得演示数据和线上算出来的两套口径
from .routers.records import _import_part
from .routers.staff_ops import compute_hours

random.seed(42)

# 真人联调专用的学号（学校系统是生产环境，只能用这一个号试），
# 演示数据里绝不能出现——见 main() 末尾那道检查
RESERVED_STUDENT_IDS = {"2025201717"}

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
    """演示用志愿者联络群二维码（内联 SVG，不依赖外部文件）。

    创建活动时二维码是必填项，报名成功后要弹给志愿者看，种子数据不给的话
    弹窗就是一片空白，看起来像功能没做。
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
            # 老师首次登录即是活动发起方（见 routers/auth.py 的 _provision），演示数据保持一致
            ("2025001", "王砚秋", "teacher", ["admin", "qingxie", "initiator"]),  # 管理员：见全部
            ("2025002", "李慕清", "teacher", ["initiator"]),          # 发起方：仅自己管理的活动
            ("2025003", "张远航", "student", ["qingxie", "student"]), # 青协：会长团
            ("2025004", "周砚", "student", ["qingxie", "student"]),     # 青协：对外联络部部长
            ("2025005", "林骁", "student", ["qingxie", "student"]),     # 青协：对外联络部骨干
            ("2025006", "苏昀", "student", ["qingxie", "student"]),     # 青协：对外联络部成员（学习权限）
        ]
        users: dict[str, User] = {}
        for sid, name, utype, roles in staff_specs:
            u = User(id=uid("u"), student_id=sid, name=name,
                     gender="女" if name[1] in "砚慕" else "男",
                     college="校团委" if "admin" in roles else "校青年志愿者协会",
                     campus="中关村校区",
                     stage="" if utype == "teacher" else "硕士研究生",
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

        # 部门与职务：审批按部门归口，部长是默认审批人，会长可接管任何一条
        from backend.routers.approvals import ensure_departments
        from backend.models import Department
        ensure_departments(db)
        zhang.qingxie_post = "council"
        wang.qingxie_post = "council"      # 管理员同时在会长团
        liaison = (db.query(Department)
                   .filter(Department.name == "对外联络部").one())
        for sid, post in (("2025004", "head"), ("2025005", "core"),
                          ("2025006", "member")):
            users[sid].department_id = liaison.id
            users[sid].qingxie_post = post
        # 成员跟着骨干：苏昀看到的活动与林骁一模一样
        users["2025006"].supervisor_id = users["2025005"].id
        # 对外联络部启用值班（见 liaison.py），但种子里不预设值班人：没人值班时
        # 审批通知回落给部长，演示时由周砚在「活动审批」页指定林骁值班，两种情形都看得到
        db.commit()

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
                     # 校区/学段轮着发：招募范围与场次条件要筛得出人，
                     # 全塞同一个值的话，一加条件就是「谁都报不了」或「谁都能报」
                     campus=config.CAMPUSES[i % len(config.CAMPUSES)],
                     stage=config.STAGES[i % len(config.STAGES)],
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

        # 留一位没报过任何活动的学生，演示「干净档案」
        spare = students[11]
        db.commit()

        # ---------- 岗位助手（无时间：职责/地点/名额/要求） ----------
        # 服务地点/要求原文/培训内容都是必填，演示数据也要满足——
        # 否则后台一改岗位就被自己的校验拦下
        def stn(shift, name, loc, quota, tags=None,
                note="服从现场安排，提前 15 分钟到岗",
                train="到岗后由负责人统一讲解岗位职责与应急流程",
                leader=False, waitlist=3, attire="none", attire_note=""):
            p = Position(id=uid("p"), shift_id=shift.id, name=name,
                         location=loc, quota=quota, waitlist_quota=waitlist,
                         requirement_tags=tags or {}, service_task=note,
                         training_note=train, needs_leader=leader,
                         attire=attire, attire_note=attire_note)
            db.add(p)
            return p

        def enr(user, act, group, days_ago, status="enrolled", position=None,
                assigned=True, allow_reassign=None):
            e = Enrollment(id=uid("enr"), activity_id=act.id,
                           shift_id=group.id, user_id=user.id,
                           position_id=position.id if position else None,
                           status=status, source="self",
                           # 约四分之一的人不接受调剂，分配面板上能看到提示
                           allow_reassign=(True if status != "enrolled"
                                           else (random.random() > 0.25
                                                 if allow_reassign is None
                                                 else allow_reassign)),
                           leader_willing=random.random() > 0.6,
                           joined_at=dt(days=-days_ago,
                                        minutes=random.randint(-50, 50)))
            if status == "enrolled" and assigned and position:
                e.assigned_at = e.joined_at
            db.add(e)
            return e

        # ---------- 活动1：运动会（报名中·报名即选岗位） ----------
        sport = Activity(id=uid("act"), title="校运动会开幕式志愿服务", organizer="校青年志愿者协会",
                         recruit_mode="instant", cover_hue=350,
                         intro="全校秋季运动会开幕式需要大量志愿者支持，涉及引导、检录、礼仪、医疗辅助等岗位。"
                               "请仔细核对时间后报名；如无法参加请提前 24 小时退选。",
                         location="世纪馆田径场",
                         location_point={"lat": 39.96785, "lng": 116.30905},
                         contacts=[{"name": "李慕清", "phone": "138xxxx0002"},
                                   {"name": "王砚秋", "phone": "13950010000"}],
                         contact_group_qr=demo_qr("YVRUC-CONTACT-GROUP"),
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
                     "熟悉校园路线，口齿清晰", "提前30分钟到东门集合培训",
                     attire="own", attire_note="白色上衣、深色长裤、运动鞋")
        s_check = stn(m1, "检录服务", "田径场检录处", 7, {},
                      "负责运动员检录与号码布发放")
        s_etiq = stn(m1, "颁奖礼仪", "主席台前区", 4,
                     {"性别": "女"}, "颁奖环节礼仪志愿",
                     "赛前一周 19:00 线上培训会议",
                     attire="issued", attire_note="当天 8:00 在主席台后物资桌领取，散场归还")
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

        # 上午场：东门满8 / 检录 6+1扣留=满7 / 礼仪3（女）/ 医疗2
        # 检录只放 6 个人：下面那位临期退选的占掉第 7 个位子。
        # 放满 7 再加一条扣留，就成了「已占用 8 > 名额 7」——真实流程走不出
        # 这种状态（要先报上名才谈得上扣留），演示数据不该自己造一个。
        for _ in range(8):
            enr(take(), sport, m1, 3, position=s_gate)
        for _ in range(6):
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

        # 一名临期退选：扣分且名额被扣留不释放（roster 里显示为占位）
        late_user = take()
        late_enr = enr(late_user, sport, m1, 1, status="cancelled",
                       position=s_check, assigned=False)
        late_enr.cancel_type = "late"
        late_enr.cancelled_at = dt(hours=-3)
        late_enr.seat_held = True

        # 下午场 13人
        for _ in range(6):
            enr(take(), sport, m2, 2, position=o_order)
        for _ in range(4):
            enr(take(), sport, m2, 2, position=o_gear)
        for _ in range(3):
            enr(take(), sport, m2, 1, position=o_final)

        # ---------- 活动2：图书整理周（报名中·截止后统一分配） ----------
        book = Activity(id=uid("act"), title="图书馆图书整理周·周六专场", organizer="图书馆",
                        recruit_mode="instant", cover_hue=210,
                        intro="图书馆馆藏调整需要大批志愿者参与图书下架、搬运与重新上架。"
                              "具体分工将在报名截止后由青协统一排班公布。",
                        location="图书馆（明德楼）",
                        contacts=[{"name": "张远航", "phone": "13950030000"}],
                        contact_group_qr=demo_qr("YVRUC-CONTACT-GROUP"),
                        status="published",
                        reg_start=dt(days=-2), reg_end=dt(days=3),
                        created_by=wang.id, created_at=dt(days=-3))
        db.add(book)
        b1 = Shift(id=uid("g"), activity_id=book.id, name="周六全天场",
                           start_time=dt(days=5, base=t0.replace(hour=9)),
                           end_time=dt(days=5, base=t0.replace(hour=17)),
                           mode="assign",
                           note="具体岗位（分拣/搬运/上架）报名截止后由排班程序统一分配",
                           sort=0)
        db.add(b1)
        db.flush()
        # 「截止后统一分配」的场次自带一个占位岗位，名额记在它头上，
        # 报名的人都落在这里，等报名截止后由一键分配挪进真实岗位
        b1_hold = Position(id=uid("p"), shift_id=b1.id, name="待分配",
                           is_placeholder=True, quota=20, waitlist_quota=3,
                           sort=-1,
                           service_task="报名截止后由负责人统一分配岗位",
                           training_note="岗位确定后另行通知")
        db.add(b1_hold)
        # 截止后再分的真实岗位可以先建好：报名的人看不到也选不了，一键分配
        # 或导入排班时把人从「待分配」挪进来
        for i, (pname, quota, task) in enumerate([
                ("分拣", 8, "按类别分拣捐赠图书，剔除破损书籍"),
                ("搬运", 6, "把分拣好的图书搬到仓库指定货架"),
                ("上架", 6, "按编号上架并登记")]):
            db.add(Position(id=uid("p"), shift_id=b1.id, name=pname, quota=quota,
                            sort=i, service_task=task, training_note="到场后统一讲解"))
        db.flush()
        spare_id = spare.id
        cnt = 0
        for u in students:
            if cnt >= 18 or u.id == spare_id:
                continue
            db.add(Enrollment(id=uid("enr"), activity_id=book.id,
                              shift_id=b1.id, user_id=u.id, status="enrolled",
                              source="self", position_id=b1_hold.id,
                              allow_reassign=random.random() > 0.25,
                              joined_at=dt(days=-2, minutes=cnt * 7)))
            cnt += 1

        # ---------- 活动3：招生咨询会（报名中·一个场次一个岗位） ----------
        fair = Activity(id=uid("act"), title="校园开放日·招生咨询志愿讲解", organizer="本科生招生办公室",
                        recruit_mode="instant", cover_hue=150,
                        intro="面向来访考生家长的咨询引导志愿服务。岗位简单，无需分岗位，"
                              "报名即锁定时段。",
                        location="明德广场",
                        location_point={"lat": 39.96921, "lng": 116.30772},
                        contacts=[{"name": "张远航", "phone": "13950030000"}],
                        contact_group_qr=demo_qr("YVRUC-CONTACT-GROUP"),
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
                              shift_id=f1.id, user_id=u.id, status="enrolled",
                              position_id=f1_pos.id, source="self",
                              assigned_at=dt(days=-1),
                              joined_at=dt(days=-1)))
        rest = [u for u in students
                if u.id not in book_ids and u.id != spare_id][:4]
        for u in rest:
            db.add(Enrollment(id=uid("enr"), activity_id=fair.id,
                              shift_id=f1.id, user_id=u.id, status="enrolled",
                              position_id=f1_pos.id, source="self",
                              assigned_at=dt(days=-1),
                              joined_at=dt(days=-1)))

        # ---------- 活动4：校庆晚会当日保障（已截止·签到进行中·今天全天） ----------
        gala = Activity(id=uid("act"), title="校庆文艺晚会·当日保障", organizer="校团委",
                        recruit_mode="instant", cover_hue=280,
                        intro="校庆晚会当天全流程保障，已截止报名。请按分配岗位准时到岗并扫码签到。",
                        location="明德堂",
                        contacts=[{"name": "王砚秋", "phone": "13950010000"},
                                  {"name": "张远航", "phone": "13950030000"}],
                        contact_group_qr=demo_qr("YVRUC-CONTACT-GROUP"),
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
                              shift_id=c1.id, user_id=u.id, status="enrolled",
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

        # ---------- 活动5：敬老院（已完成·时长已锁定） ----------
        elder = Activity(id=uid("act"), title="社区敬老院陪伴服务（秋）", organizer="文学院青年志愿者协会",
                         recruit_mode="instant", cover_hue=30,
                         intro="每周定期前往附近社区敬老院陪伴老人，聊天读报、组织小活动。"
                               "岗位简单，仅按场次报名。",
                         location="海淀街道敬老院",
                         contacts=[{"name": "李慕清", "phone": "138xxxx0002"}],
                         contact_group_qr=demo_qr("YVRUC-CONTACT-GROUP"),
                         status="completed", reg_start=dt(days=-20),
                         hours_approved_at=dt(days=-6),
                         appeal_deadline=dt(days=-5),
                         hours_locked_at=dt(days=-5),
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
                              shift_id=e1.id, user_id=u.id, status="enrolled",
                              joined_at=joined, assigned_at=joined))
            db.add(CheckinRecord(id=uid("ckr"), task_id=elder_task.id,
                                 activity_id=elder.id, shift_id=e1.id,
                                 user_id=u.id, method="scan",
                                 checkin_at=e1.start_time + timedelta(minutes=5 + i)))

        # 已确认的时长也走同一套算法，不在这儿另写一份：备注就是明细
        # （签到签退时间 + 岗位名）——手写的那份跟实际算出来的迟早对不上
        db.flush()
        for row in compute_hours(db, elder):
            db.add(HoursRecord(id=uid("hrs"), activity_id=elder.id,
                               user_id=row["user"]["id"], hours=row["hours"],
                               parts=row["parts"], confirmed_by=wang.id))

        db.commit()

        # ---------- 活动6：迎新周（报名中·报名后筛选） ----------
        # 志愿者填一份表、勾意向场次；截止后由管理者录取并安排岗位
        fresh = Activity(id=uid("act"), title="新生迎新周·志愿者选拔",
                         organizer="校青年志愿者协会",
                         recruit_mode="screened", cover_hue=190,
                         intro="迎新周需要一批能独立带队的志愿者，请如实填写报名问卷；"
                               "报名截止后由青协统一筛选，录取结果通过企业微信通知。",
                         location="东门迎新广场",
                         contacts=[{"name": "李慕清", "phone": "138xxxx0002"}],
                         contact_group_qr=demo_qr("YVRUC-CONTACT-GROUP"),
                         status="published",
                         reg_start=dt(days=-2), reg_end=dt(days=5),
                         created_by=limu.id, created_at=dt(days=-3))
        db.add(fresh)
        db.flush()
        fresh_shifts, holders = [], []
        for i, (name, h0, h1) in enumerate([("报到日上午", 8, 12), ("报到日下午", 13, 17)]):
            g = Shift(id=uid("g"), activity_id=fresh.id, name=name,
                      start_time=dt(days=8, base=t0.replace(hour=h0, minute=0)),
                      end_time=dt(days=8, base=t0.replace(hour=h1, minute=0)),
                      mode="assign", sort=i, location="东门迎新广场")
            db.add(g)
            db.flush()
            holder = Position(id=uid("p"), shift_id=g.id, name="待分配",
                              is_placeholder=True, quota=12, waitlist_quota=3,
                              sort=-1, location="东门迎新广场",
                              service_task="录取后由负责人安排具体岗位",
                              training_note="到岗后统一讲解")
            db.add(holder)
            holders.append(holder)
            fresh_shifts.append(g)
        db.flush()
        q_exp = FormQuestion(id=uid("q"), activity_id=fresh.id, kind="single",
                             title="是否参加过迎新志愿服务",
                             options=["参加过", "没有参加过"], sort=0)
        q_job = FormQuestion(id=uid("q"), activity_id=fresh.id, kind="multi",
                             title="可承担的工作（可多选）",
                             options=["引导带路", "行李搬运", "报到登记", "外语接待"], sort=1)
        q_intro = FormQuestion(id=uid("q"), activity_id=fresh.id, kind="text",
                               title="一句话介绍自己", sort=2)
        q_file = FormQuestion(id=uid("q"), activity_id=fresh.id, kind="file",
                              title="相关证明材料（选填）", required=False, sort=3)
        db.add_all([q_exp, q_job, q_intro, q_file])
        db.flush()
        for i, u in enumerate(students[28:34]):
            picked = (list(range(len(fresh_shifts))) if i % 3 == 0
                      else [i % 2])          # 意向岗位：这里每场只有「待分配」
            db.add(Application(id=uid("app"), activity_id=fresh.id, user_id=u.id,
                               answers={q_exp.id: "参加过" if i % 2 else "没有参加过",
                                        q_job.id: ["引导带路", "报到登记"][: 1 + i % 2],
                                        q_intro.id: f"我是{u.college}的{u.name}，"
                                                    f"熟悉校园，愿意全程参与。"},
                               wanted_positions=[holders[k].id for k in picked],
                               created_at=dt(days=-1, minutes=i * 40)))
            for k in picked:
                db.add(Enrollment(id=uid("enr"), activity_id=fresh.id,
                                  shift_id=fresh_shifts[k].id, user_id=u.id,
                                  status="applied", source="self",
                                  position_id=holders[k].id,
                                  joined_at=dt(days=-1, minutes=i * 40)))
        db.commit()

        # ---------- 活动7：学院读书会（补记录·已结束·名单已导入） ----------
        # 线下已经办完的活动：不招人、不签到，审批通过即「已结束」，
        # 管理者导入名单后核准时长
        club = Activity(id=uid("act"), title="学院读书会志愿服务",
                        organizer="文学院青年志愿者协会",
                        recruit_mode="record", cover_hue=60,
                        intro="文学院读书会的场地布置与签到引导，活动已在线下办完，"
                              "这里补登记参与名单与志愿时长。",
                        location="人文楼报告厅",
                        contacts=[{"name": "李慕清", "phone": "138xxxx0002"}],
                        status="finished",
                        held_start=dt(days=-10, base=t0.replace(hour=14)),
                        held_end=dt(days=-10, base=t0.replace(hour=17)),
                        approved_at=dt(days=-9),
                        created_by=limu.id, created_at=dt(days=-12))
        db.add(club)
        db.flush()
        # 导入的那一段明细与真导入同一个写法，不在这儿另写一份
        for i, u in enumerate(students[40:46]):
            hours = 3.0 if i % 3 else 2.5
            note = "场地布置" if i % 2 else "签到引导"
            db.add(HoursRecord(id=uid("hrs"), activity_id=club.id, user_id=u.id,
                               hours=hours, source="import", note=note,
                               parts=[_import_part(club, hours, note)],
                               confirmed_by=limu.id))
        db.commit()

        # ---------- 活动管理者（创建者自动成为管理者，另配多人共管） ----------
        acts_by_title = {a.title: a for a in db.query(Activity).all()}
        mgr_plan = [
            ("校运动会开幕式志愿服务", [wang, limu]),
            ("图书馆图书整理周·周六专场", [wang, zhang]),
            ("校园开放日·招生咨询志愿讲解", [zhang]),
            ("校庆文艺晚会·当日保障", [zhang]),
            ("社区敬老院陪伴服务（秋）", [limu]),
            ("新生迎新周·志愿者选拔", [limu, wang]),
            ("学院读书会志愿服务", [limu]),
        ]
        for title, mgrs in mgr_plan:
            act = acts_by_title[title]
            for m in mgrs:
                db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                                       user_id=m.id, added_by=act.created_by))
        db.commit()

        # ---------- 审计留痕 ----------
        # 走真正的 audit.log 写留痕，只把时间改成过去：visible 的口径
        # （默认不给志愿者看，只有公开后的结构性变更才标 True）由它一处说了算，
        # 种子里不另抄一份。签到码、分配结果、时长、违规都是内部留痕。
        def aud(action, target_type, target_id, label, act_id, summary,
                when, changes=None, visible=False, actor=None):
            row = audit_log(db, actor=actor or wang, action=action,
                            target_type=target_type, target_id=target_id,
                            target_label=label, activity_id=act_id,
                            summary=summary, changes=changes, visible=visible)
            row.created_at = when

        aud("create", "activity", sport.id, sport.title, sport.id,
            "创建活动「校运动会开幕式志愿服务」", dt(days=-4))
        aud("publish", "activity", sport.id, sport.title, sport.id,
            "发布活动，开始报名", dt(days=-3), visible=True)
        aud("update", "group", m1.id, "运动会·上午场", sport.id,
            "集合地点由「南门」变更为「东门」",
            dt(days=-1, hours=-6),
            [{"field": "note", "label": "备注",
              "old": "志愿者南门集合", "new": "志愿者东门集合"}], visible=True)
        aud("update", "position", s_etiq.id, "运动会·颁奖礼仪", sport.id,
            "礼仪岗增加性别要求（女），并补充线上培训安排",
            dt(days=-2, hours=3),
            [{"field": "requirement_tags", "label": "条件要求",
              "old": "不限", "new": "性别=女"},
             {"field": "training_note", "label": "培训内容",
              "old": "", "new": "赛前一周 19:00 线上培训会议"}], visible=True)
        aud("create", "activity", book.id, book.title, book.id,
            "创建活动「图书馆图书整理周·周六专场」（截止后统一分配岗位）",
            dt(days=-3))
        aud("publish", "activity", book.id, book.title, book.id,
            "发布活动，开始报名", dt(days=-2), visible=True)
        aud("assign", "assignment", c1.id, "晚会·当日班", gala.id,
            "12 人完成岗位分配（其中强制分配 0 人）", dt(days=-5),
            actor=users["2025003"])
        aud("create", "checkin", task_gala.id, "晚会签到码 GALA01", gala.id,
            "生成统一签到码 GALA01", dt(days=-1),
            actor=users["2025003"])
        aud("create", "activity", elder.id, elder.title, elder.id,
            "创建活动「社区敬老院陪伴服务（秋）」",
            dt(days=-22))
        aud("hours_confirm", "hours", elder.id, elder.title, elder.id,
            "锁定志愿时长，活动标记为已完成（申诉截止无人申诉，系统自动锁定）",
            dt(days=-5))
        vio_user = students[23]
        db.add(ViolationRecord(id=uid("vio"), user_id=vio_user.id,
                               activity_id=elder.id, activity_title=elder.title,
                               kind="late", detail="迟到 40 分钟，未提前告知",
                               operator_id=wang.id, created_at=dt(days=-15)))
        aud("violation", "user", vio_user.id,
            f"{vio_user.name}（{vio_user.student_id}）", elder.id,
            f"违规登记：迟到早退「{elder.title}」——迟到 40 分钟，未提前告知",
            dt(days=-15))
        db.commit()

        # ---------- 通知样例 ----------
        def notify(u, key, title, content, when, read=False, act_id=""):
            db.add(Notification(id=uid("ntf"), user_id=u.id, template_key=key,
                                title=title, content=content, read=read,
                                data={"activity_id": act_id} if act_id else {},
                                created_at=when))

        notify(pc_user, "waitlist_promoted", "候补转正待确认｜校运动会开幕式志愿服务",
               f"「校运动会开幕式志愿服务」· 上午场 有名额释放，您已按候补顺序转正！\n"
               f"请在 {e_pc.confirm_deadline:%m-%d %H:%M} 前确认，超时名额顺延。",
               dt(hours=-1), act_id=sport.id)
        notify(wl_user, "waitlist_queued", "已加入候补｜校运动会开幕式志愿服务",
               "「校运动会开幕式志愿服务」· 上午场 名额已满，您已加入候补队列，当前第 2 位。",
               dt(days=-1))
        for u in students[3:6]:
            notify(u, "signup_success", "志愿活动报名成功｜校运动会开幕式志愿服务",
                   "您已成功报名「校运动会开幕式志愿服务」，时段已锁定。\n"
                   "如无法参加请提前24小时退选。", dt(days=-2, hours=-3),
                   read=bool(random.random() < 0.5))
        db.commit()
        # 演示数据里不许出现真人联调用的那个号：学号是认人的唯一凭据，
        # 种子里的「他」和本人登录后自动建出来的档案撞在一起，签到、时长、
        # 申诉就都分不清是谁的了。现在生成规则是 2022/2023/2024 开头，
        # 撞不上；哪天改了年级池，这里会当场拦下来。
        reserved = [u.student_id for u in db.query(User).all()
                    if u.student_id in RESERVED_STUDENT_IDS]
        if reserved:
            raise SystemExit(f"演示数据里出现了保留学号 {reserved}，请先改掉生成规则")
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
        print("  空档案演示账号:", spare.student_id, spare.name)
    finally:
        db.close()


if __name__ == "__main__":
    main()
