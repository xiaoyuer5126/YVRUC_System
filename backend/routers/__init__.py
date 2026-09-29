"""路由汇总。"""
from . import (activities, approvals, auth, checkin, feedback, geo, liaison,
               materials, me, roles, screening, staff_activities, staff_admin,
               staff_ops, attire)

all_routers = [
    auth.router, activities.router, me.router, checkin.router,
    staff_activities.router, staff_ops.router,
    materials.router, staff_admin.router,
    attire.router, attire.staff, approvals.router, roles.router,
    screening.router, liaison.router, geo.router, feedback.router,
]
