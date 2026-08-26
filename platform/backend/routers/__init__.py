"""路由汇总。"""
from . import activities, auth, checkin, me, staff_activities, staff_admin, staff_ops

all_routers = [
    auth.router, activities.router, me.router, checkin.router,
    staff_activities.router, staff_ops.router, staff_admin.router,
]
