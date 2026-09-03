"""路由汇总。"""
from . import (activities, approvals, auth, checkin, materials, me, roles,
               screening, staff_activities, staff_admin, staff_ops, vests)

all_routers = [
    auth.router, activities.router, me.router, checkin.router,
    staff_activities.router, staff_ops.router,
    materials.router, staff_admin.router,
    vests.router, vests.staff, approvals.router, roles.router,
    screening.router,
]
