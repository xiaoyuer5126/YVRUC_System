"""平台全局配置。

数据库默认用 Docker 容器里的 PostgreSQL（platform/docker-compose.yml，
端口 5433，账号 rucva/rucva_dev）。可用环境变量 RUCVA_DATABASE_URL 覆盖；
Postgres 不可达时自动降级 SQLite（data/rucva.db），保证演示不因环境中断。

所有与外部系统（数智人大 / 企业微信 / 签到系统 / SSO）的对接参数不写死在这里，
存数据库 integration_configs 表，管理员可在「集成设置」页在线修改——
换真实接口时不用改代码、不用重启。
"""
from __future__ import annotations

import os
from pathlib import Path

PLATFORM_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PLATFORM_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

PG_URL = "postgresql+psycopg://rucva:rucva_dev@127.0.0.1:5433/rucva"
SQLITE_URL = f"sqlite:///{(DATA_DIR / 'rucva.db').as_posix()}"

# 演示库：环境变量 > 容器 Postgres（database.py 里做可达性探测与降级）
DATABASE_URL = os.environ.get("RUCVA_DATABASE_URL", PG_URL)

# schedule 排班程序所在目录（复用其求解器 core.solve）
SCHEDULE_DIR = PLATFORM_DIR.parent / "schedule"

# 会话令牌有效期（天）。演示够用；接 SSO 后由统一身份认证接管。
SESSION_DAYS = 7

# —— 业务规则常量（与《志愿者服务规范.docx》《设计方案》对齐） ——
CREDIT_FULL = 100          # 信用分满分
CREDIT_FREEZE_BELOW = 60   # 低于该分数冻结报名
CREDIT_FREEZE_DAYS = 30    # 冻结时长（天）
LATE_CANCEL_PENALTY = 5    # 开始前 24 小时内退选扣分
LATE_CANCEL_HOURS = 24     # 「临近」阈值（小时）
PROMOTE_CONFIRM_HOURS = 2  # 候补转正确认窗口（小时）
MAX_ABANDON_TIMES = 3      # 连续放弃候补次数上限，超过暂停本学期候补资格
ABANDON_BAN_DAYS = 120     # 「本学期」按 120 天近似

# 活动类型（《志愿者服务规范》限制准入标准第一节）
ACTIVITY_TYPES = {
    "A": "常规类型单次活动",
    "B": "常规类型长期活动",
    "C": "大型校级活动志愿服务",
    "D": "重大活动",
}

VIOLATION_TYPES = ["迟到早退", "无故请假", "无法取得联系", "消极志愿", "其他"]

# 学院清单（演示用，覆盖主要书院/学院）
COLLEGES = [
    "明理书院", "新闻学院", "法学院", "财政金融学院", "经济学院",
    "信息学院", "外国语学院", "马克思主义学院", "社会学院", "商学院",
    "统计学院", "劳动人事学院",
]
