"""路由汇总。"""
from . import (activities, ai, approvals, auth, checkin, feedback, geo,
               hours_claims, liaison, materials, me, platform, records, roles,
               screening, staff_activities, staff_admin, staff_ops, attire)

all_routers = [
    auth.router, activities.router, me.router, checkin.router,
    staff_activities.router, staff_ops.router,
    materials.router, staff_admin.router,
    attire.router, attire.staff, approvals.router, roles.router,
    screening.router, liaison.router, geo.router, feedback.router,
    records.router, ai.router, platform.router, hours_claims.router,
]
