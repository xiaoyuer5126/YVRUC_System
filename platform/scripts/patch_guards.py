# 一次性补丁：给 staff_activities / staff_ops 的活动级端点加管理权校验
import pathlib

p = pathlib.Path(r"F:\RUCVA_system\platform\backend\routers\staff_activities.py")
src = p.read_text(encoding="utf-8")

DOC_DELETE = '"""仅草稿可删——发布过的活动承载着报名记录与留痕，不允许物理删除。"""'

repl = [
    # update_activity
    ("""def update_activity(activity_id: str, body: dict, bg: BackgroundTasks,
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    act = _act(db, activity_id)""",
     """def update_activity(activity_id: str, body: dict, bg: BackgroundTasks,
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)"""),
    # publish
    ("""def publish(activity_id: str, db: OrmSession = Depends(db_session),
            user: User = Depends(require_manager)):
    act = _act(db, activity_id)""",
     """def publish(activity_id: str, db: OrmSession = Depends(db_session),
            user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)"""),
    # _transition
    ("""                allowed_from: tuple[str, ...], db: OrmSession, user: User,
                bg: BackgroundTasks):
    act = _act(db, activity_id)""",
     """                allowed_from: tuple[str, ...], db: OrmSession, user: User,
                bg: BackgroundTasks):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)"""),
    # delete_activity
    (f'    {DOC_DELETE}\n    act = _act(db, activity_id)',
     f'    {DOC_DELETE}\n    ensure_activity_manager(db, user, activity_id)\n    act = _act(db, activity_id)'),
    # create_group
    ("""def create_group(activity_id: str, body: GroupBody, bg: BackgroundTasks,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    act = _act(db, activity_id)""",
     """def create_group(activity_id: str, body: GroupBody, bg: BackgroundTasks,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)"""),
    # update_group
    ("""    g = _group(db, group_id)
    act = db.get(Activity, g.activity_id)
    before_raw = {k: getattr(g, k) for k in GROUP_FIELDS}""",
     """    g = _group(db, group_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    before_raw = {k: getattr(g, k) for k in GROUP_FIELDS}"""),
    # delete_group
    ("""    g = _group(db, group_id)
    act = db.get(Activity, g.activity_id)
    n_active = db.query(Enrollment).filter(""",
     """    g = _group(db, group_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    n_active = db.query(Enrollment).filter("""),
    # create_station
    ("""    g = _group(db, group_id)
    act = db.get(Activity, g.activity_id)
    s = Station(id=uid("s"), group_id=g.id, name=body.name.strip(),""",
     """    g = _group(db, group_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    s = Station(id=uid("s"), group_id=g.id, name=body.name.strip(),"""),
    # update_station
    ("""    g = _group(db, s.group_id)
    act = db.get(Activity, g.activity_id)
    before_raw = {k: getattr(s, k) for k in STATION_FIELDS}""",
     """    g = _group(db, s.group_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    before_raw = {k: getattr(s, k) for k in STATION_FIELDS}"""),
    # delete_station
    ("""    g = _group(db, s.group_id)
    act = db.get(Activity, g.activity_id)
    n = db.query(Enrollment).filter(Enrollment.station_id == s.id,""",
     """    g = _group(db, s.group_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    n = db.query(Enrollment).filter(Enrollment.station_id == s.id,"""),
    # roster
    ("""    g = _group(db, group_id)
    act = db.get(Activity, g.activity_id)
    stations = sorted(g.stations, key=lambda s: s.sort)""",
     """    g = _group(db, group_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    stations = sorted(g.stations, key=lambda s: s.sort)"""),
    # assign preview
    ("""    g = _group(db, group_id)
    if not g.stations:
        raise HTTPException(400, "请先在该场次下创建二级工位，再运行智能排班")""",
     """    g = _group(db, group_id)
    ensure_activity_manager(db, user, g.activity_id)
    if not g.stations:
        raise HTTPException(400, "请先在该场次下创建二级工位，再运行智能排班")"""),
    # assign apply
    ("""    g = _group(db, group_id)
    if not body.plan:
        raise HTTPException(400, "空方案")""",
     """    g = _group(db, group_id)
    ensure_activity_manager(db, user, g.activity_id)
    if not body.plan:
        raise HTTPException(400, "空方案")"""),
    # walkin
    ("""    g = _group(db, group_id)
    sid = (body.get("student_id") or "").strip()""",
     """    g = _group(db, group_id)
    ensure_activity_manager(db, user, g.activity_id)
    sid = (body.get("student_id") or "").strip()"""),
    # managers endpoints (append before roster section)
    ("# ======================= 报名名单 =======================",
     """# ======================= 活动管理者 =======================

@router.get("/activities/{activity_id}/managers")
def list_managers(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    ids = activity_manager_ids(db, activity_id)
    users = [db.get(User, i) for i in ids]
    return [{
        "id": u.id, "name": u.name, "student_id": u.student_id,
        "college": u.college, "roles": u.roles or [],
        "role_label": u.active_role_label(),
        "is_creator": u.id == act.created_by,
    } for u in users if u]


class ManagerBody(BaseModel):
    student_id: str


@router.post("/activities/{activity_id}/managers")
def add_activity_manager(activity_id: str, body: ManagerBody,
                         db: OrmSession = Depends(db_session),
                         user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    target = db.query(User).filter(
        User.student_id == body.student_id.strip()).first()
    if target is None:
        raise HTTPException(404, f"学号 {body.student_id} 不存在")
    row = add_manager(db, activity_id, target, user)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=activity_id, target_label=act.title,
              activity_id=activity_id,
              summary=f"添加活动管理者：{target.name}（{target.student_id}）",
              visible=False)
    db.commit()
    return {"ok": True, "id": row.id}


@router.delete("/activities/{activity_id}/managers/{user_id}")
def remove_activity_manager(activity_id: str, user_id: str,
                            db: OrmSession = Depends(db_session),
                            user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    target = db.get(User, user_id)
    remove_manager(db, activity_id, user_id, act.created_by)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=activity_id, target_label=act.title,
              activity_id=activity_id,
              summary=f"移除活动管理者：{target.name if target else user_id}"
                      + ("（创建者不可移除）" if target and target.id == act.created_by else ""),
              visible=False)
    db.commit()
    return {"ok": True}


# ======================= 报名名单 ======================="""),
]

missed = []
for old, new in repl:
    if old in src:
        src = src.replace(old, new, 1)
    else:
        missed.append(old[:60])

p.write_text(src, encoding="utf-8")
print("done; missed:", len(missed))
for m in missed:
    print("  MISS:", m.replace("\n", "\\n")[:80])

# ---- staff_ops.py ----
p2 = pathlib.Path(r"F:\RUCVA_system\platform\backend\routers\staff_ops.py")
s2 = p2.read_text(encoding="utf-8")
s2 = s2.replace("from ..deps import require_staff",
                "from ..deps import require_manager")
s2 = s2.replace("Depends(require_staff)", "Depends(require_manager)")
repl2 = [
    # create_task (group -> activity)
    ("""    g = db.get(PositionGroup, group_id)
    if g is None:
        raise HTTPException(404, "岗位组不存在")
    act = db.get(Activity, g.activity_id)
    station = None""",
     """    g = db.get(PositionGroup, group_id)
    if g is None:
        raise HTTPException(404, "岗位组不存在")
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    station = None"""),
    # list_tasks
    ("""def list_tasks(activity_id: str, db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    _act(db, activity_id)""",
     """def list_tasks(activity_id: str, db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    _act(db, activity_id)"""),
    # toggle_task (task -> activity)
    ("""    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    if "active" in body:""",
     """    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    if "active" in body:"""),
    # board
    ("""    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    g = db.get(PositionGroup, task.group_id)
    act = db.get(Activity, task.activity_id)
    expected""",
     """    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    g = db.get(PositionGroup, task.group_id)
    act = db.get(Activity, task.activity_id)
    expected"""),
    # manual_checkin
    ("""    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    u = db.query(User).filter(""",
     """    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    u = db.query(User).filter("""),
    # export
    ("""    act = _act(db, activity_id)
    buf = build_activity_workbook(db, act)""",
     """    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    buf = build_activity_workbook(db, act)"""),
    # hours preview/confirm/upload share _act; patch each individually
    ("""def hours_preview(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    act = _act(db, activity_id)""",
     """def hours_preview(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)"""),
    ("""    act = _act(db, activity_id)
    items = compute_hours(db, act)""",
     """    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    items = compute_hours(db, act)"""),
    ("""    act = _act(db, activity_id)
    rows = (db.query(HoursRecord)""",
     """    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    rows = (db.query(HoursRecord)"""),
]
missed2 = []
for old, new in repl2:
    if old in s2:
        s2 = s2.replace(old, new, 1)
    else:
        missed2.append(old[:50])
s2 = s2.replace("from ..exporter import build_activity_workbook",
                "from ..exporter import build_activity_workbook\nfrom ..perm import ensure_activity_manager")
p2.write_text(s2, encoding="utf-8")
print("staff_ops done; missed:", len(missed2))
for m in missed2:
    print("  MISS:", m.replace("\n", "\\n")[:80])
