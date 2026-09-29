"""对外联络部：值班、志愿北京登记、立项书。

二级志愿团体的活动都归口对外联络部，审批量远超其他部门，部长一个人盯不过来：
由部门骨干轮流值班，这个部门的审批（活动发布、时长锁定/解锁、志愿北京登记）
通知只发给值班的人，值班的人和部长一样能拍板；部长每晚收一份日报。
没人值班就回落给部长——审批不能因为排班空档没人收。

值班按部门做成通用的（Department.duty_user_id），但只在 DUTY_DEPTS 里的部门
启用；别的部门行为一概不变。部门按名字认：部门是页面上可以增删改的数据，
没有别的稳定标识，改了名就等于关掉值班——宁可关掉，也别套到别的部门上。

「对外活动」= 归口对外联络部的活动：提交审批要带立项书，时长报批要带盖章立项书。
两份文件都挂在 Attachment 上（owner_type = proposal / stamped，owner_id = 活动 id），
存储与一次性票据下载沿用学习材料那一套。

志愿北京时长录入截图（vb_hours）也按同一套格子放：它不分内外，所有活动锁定
时长之后都要交，交了并确认，活动才算「已完成」。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from .models import Activity, Attachment, Department, User

LIAISON_DEPT = "对外联络部"
DUTY_DEPTS = (LIAISON_DEPT,)
# 能值班的：部门骨干与部长。部门成员是学习权限，拍不了板。
ELIGIBLE_POSTS = ("core", "head")

SLOTS = {"proposal": "立项书", "stamped": "盖章立项书",
         "vb_hours": "志愿北京时长录入截图"}
# 各格能传的文件类型；没列的格子沿用学习材料的全部类型。截图就是截图：
# 图片，或者打印成的 PDF
SLOT_EXTS = {"vb_hours": ("png", "jpg", "jpeg", "pdf")}


# ======================= 部门与值班 =======================

def duty_enabled(dept: Department | None) -> bool:
    return dept is not None and dept.name in DUTY_DEPTS


def liaison_dept(db: OrmSession) -> Department | None:
    return db.query(Department).filter(Department.name == LIAISON_DEPT).first()


def dept_head(db: OrmSession, dept_id: str) -> User | None:
    return (db.query(User)
            .filter(User.department_id == dept_id,
                    User.qingxie_post == "head").first())


def is_head_of(user: User, dept: Department | None) -> bool:
    """当前操作者是不是这个部门的部长（跟当前身份：切到别的身份就不算）。"""
    return dept is not None and user.department_id == dept.id and user.is_head()


def eligible(user: User | None, dept: Department, *, real: bool = True) -> bool:
    """能不能在这个部门值班：本部门的骨干或部长。

    默认按真实身份：这是这个人的资格，值班人是谁、能交班给谁都看它。
    同一个请求里查出来的值班人可能正是当前用户本人（带着身份视角），按视角
    算的话，他切到管理员身份去操作，自己的值班就「失效」了、通知改发给部长。
    问的是「当前操作者此刻能不能以值班人身份办事」时传 real=False。
    """
    return (user is not None and user.department_id == dept.id
            and user.post(real=real) in ELIGIBLE_POSTS)


def eligible_users(db: OrmSession, dept: Department) -> list[User]:
    rows = (db.query(User).filter(User.department_id == dept.id,
                                  User.qingxie_post.in_(ELIGIBLE_POSTS))
            .order_by(User.student_id).all())
    return [u for u in rows if eligible(u, dept)]


def on_duty(db: OrmSession, dept: Department | None) -> User | None:
    """当前值班人。人被调走或降成成员之后，他的值班自动失效——
    不然通知会一直发给一个已经不在这个部门、也拍不了板的人。"""
    if not duty_enabled(dept) or not dept.duty_user_id:
        return None
    u = db.get(User, dept.duty_user_id)
    return u if eligible(u, dept) else None


def approval_contact(db: OrmSession, dept_id: str | None) -> User | None:
    """这个部门的审批该通知谁：启用值班的部门先找值班人，没人值班找部长；
    其他部门照旧是部长。"""
    dept = db.get(Department, dept_id) if dept_id else None
    if dept is None:
        return None
    return on_duty(db, dept) or dept_head(db, dept.id)


def is_dept_handler(db: OrmSession, user: User, dept_id: str | None) -> bool:
    """能以部门的名义拍这个部门的审批：值班人与部长（仅限启用值班的部门）。

    未启用值班的部门返回 False，那边仍然只认「当前审批人」，行为不变。
    """
    dept = db.get(Department, dept_id) if dept_id else None
    if not duty_enabled(dept):
        return False
    if is_head_of(user, dept):
        return True
    return is_on_duty(on_duty(db, dept), user)


def is_on_duty(cur: User | None, user: User) -> bool:
    """当前操作者就是在班的人，且此刻以青协身份在操作。"""
    return (cur is not None and cur.id == user.id
            and user.post() in ELIGIBLE_POSTS)


def handler_dept_ids(db: OrmSession, user: User) -> list[str]:
    """这个人以值班人/部长身份负责的部门。审批列表按它筛。"""
    return [d.id for d in db.query(Department)
            .filter(Department.name.in_(DUTY_DEPTS)).all()
            if is_dept_handler(db, user, d.id)]


def duty_eligible(db: OrmSession, user: User, *, real: bool = False) -> bool:
    """在某个启用值班的部门里有值班资格。前端据此露出「活动审批」入口。

    判当前操作者能不能进审批页时跟当前身份；下发给前端的是身份事实，
    session_user 传 real=True。"""
    if not user.department_id or user.post(real=real) not in ELIGIBLE_POSTS:
        return False
    return duty_enabled(db.get(Department, user.department_id))


def session_user(db: OrmSession, user: User) -> dict:
    """登录、刷新个人信息时下发的用户信息：full() 再加上值班资格。

    值班资格要查部门，放不进 User.full()——那个方法在名册列表里逐行调用，
    多一次查询就是多几百次。
    """
    return {**user.full(), "duty_eligible": duty_eligible(db, user, real=True)}


# ======================= 对外活动与立项书 =======================

# 活动正式发布之后的状态。先立项再结项：志愿北京登记、盖章立项书都是
# 活动发布之后才交的材料，草稿和审批中的活动交不了
PUBLISHED_STATES = ("published", "closed", "finished", "vb_pending", "completed")


def is_published(act: Activity) -> bool:
    return act.status in PUBLISHED_STATES


def is_external(db: OrmSession, act: Activity) -> bool:
    """对外活动：归口对外联络部。"""
    if not act.approval_dept_id:
        return False
    dept = db.get(Department, act.approval_dept_id)
    return dept is not None and dept.name == LIAISON_DEPT


def project_file(db: OrmSession, activity_id: str, slot: str) -> Attachment | None:
    return (db.query(Attachment)
            .filter(Attachment.owner_type == slot,
                    Attachment.owner_id == activity_id).first())


def drop_project_file(db: OrmSession, activity_id: str, slot: str) -> Attachment | None:
    """删掉某一格里的文件（记录与磁盘文件一起）。返回删掉的那条，没有就 None。
    不提交事务。"""
    from .routers.materials import MATERIAL_DIR

    row = project_file(db, activity_id, slot)
    if row is None:
        return None
    (MATERIAL_DIR / row.stored_name).unlink(missing_ok=True)
    db.delete(row)
    return row


def can_see_project_files(db: OrmSession, user: User, act: Activity) -> bool:
    """立项书给谁看：活动管理者，以及审批线上的人（值班人/部长/会长团/管理员、
    当前审批人与批过它的人）。志愿者看不到——活动公开了也不行。"""
    from .perm import is_activity_manager
    from .routers.approvals import can_review

    return (is_activity_manager(db, user, act.id)
            or is_dept_handler(db, user, act.approval_dept_id)
            or can_review(db, user, act))
