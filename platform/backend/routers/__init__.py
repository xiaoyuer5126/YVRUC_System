"""路由汇总。"""
from . import (activities, auth, checkin, materials, me, staff_activities,
               staff_admin, staff_ops)

all_routers = [
    auth.router, activities.router, me.router, checkin.router,
    staff_activities.router, staff_ops.router,
    materials.router, staff_admin.router,
]
