"""平台全局配置。

运行方式统一为 Docker Compose（docker-compose.yml）：
后端连容器内的 PostgreSQL 与 Redis，两者都是硬依赖，连不上直接启动失败——
半可用的服务比起不来更难排查。

与外部系统（数智人大 / 企业微信 / 签到系统 / SSO）的对接参数不写死在这里，
存数据库 integration_configs 表，管理员可在「集成设置」页在线修改。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# —— 基础设施 ——
DATABASE_URL = os.environ["RUCVA_DATABASE_URL"]
REDIS_URL = os.environ["RUCVA_REDIS_URL"]

# —— 会话 ——
SESSION_HOURS = 24          # 令牌有效期；接 SSO 后由统一身份认证接管

# 身份证号在库里是加密存的（见 crypto.py），密钥必须与数据库分开保管。
ID_CARD_KEY = os.environ.get("RUCVA_ID_CARD_KEY", "").strip()

# —— 演示登录 ——
# 仅在 SSO 未接通时开放，且必须配置共享口令；未配置则整个演示登录接口不存在。
# 学校 SSO 接通后删除 routers/auth.py 里的 demo 分支与这一项。
DEMO_LOGIN_PASSWORD = os.environ.get("RUCVA_DEMO_PASSWORD", "").strip()

# 接口文档（/docs、/openapi.json）仅在显式打开时暴露
EXPOSE_API_DOCS = os.environ.get("RUCVA_EXPOSE_DOCS", "").strip() == "1"

# 平台对外网址（多个用逗号分隔），二维码里写的就是它。列在这里的域名才允许
# 出现在二维码中——否则任何人都能拿平台的接口生成一张指向自家站点的码，
# 扫的人只看到「志愿平台发出来的二维码」。留空则只认本次请求自己的域名。
PUBLIC_BASE_URLS = [s.strip().rstrip("/") for s in
                    os.environ.get("RUCVA_PUBLIC_BASE_URL", "").split(",")
                    if s.strip()]

# 下载票据：导出与材料下载走 <a> 直链，带不了请求头，改用一次性票据。
# 短到只够浏览器立刻发起这一次下载，进了日志也用不了第二次。
DOWNLOAD_TICKET_SECONDS = 60

# 后台定时任务循环（见 main._sweeper）。测试里要关掉：每建一个 TestClient
# 都会触发一次立即扫描，它在另一个线程里跟用例抢同一批数据——用例刚把编辑态
# 改成超时，扫描线程也看见了，于是解冻通知发了两条，断言随机失败。
RUN_SWEEPER = os.environ.get("RUCVA_DISABLE_SWEEPER", "").strip() != "1"

# —— 业务规则常量 ——
# 平台不对志愿者做任何限制：没有准入名单，也不拦任何人报名。临期退选只影响
# 那个名额（报名已截止时扣留不释放），并记一次「临期退选」供青协查看。
LATE_CANCEL_HOURS = 24     # 「临近」阈值（小时）

# 内部提示分（backend/scoring.py）。只给青协工作人员看，作用仅限于在名单上
# 挂一个感叹号。权重先按这套跑，回头由部长们定夺再改这里就行——分数是现算的，
# 改了立刻全平台生效，不会留下对不上的历史值。
SCORE_BASE = 100            # 起始分，也是上限
SCORE_MIN = 0
SCORE_ALERT_BELOW = 60      # 低于它，名单上姓名旁挂感叹号
SCORE_PER_ACTIVITY = 5      # 顺利参与一次活动加回来的分
LATE_CANCEL_WEIGHT = 5      # 一次临期退选扣多少
VIOLATION_WEIGHTS = {       # 一次违规扣多少，最重一档 20
    "late": 10,       # 迟到早退
    "leave": 15,      # 无故请假
    "contact": 20,    # 无法取得联系
    "passive": 10,    # 消极志愿
    "other": 5,       # 其他
}
PROMOTE_CONFIRM_HOURS = 2  # 候补转正确认窗口（小时）

# 签退宽限：场次结束后这么久内仍可自行签退，超时按场次结束时间兜底并标记待核
CHECKOUT_GRACE_HOURS = 2

# 审批放着没人处理多久开始催（小时）。只催人，**绝不自动通过**——
# 审批不能因为没人看就默认放行。
APPROVAL_REMIND_HOURS = int(os.environ.get("RUCVA_APPROVAL_REMIND_HOURS", "24"))

# 编辑冻结的自动解除时长（小时）：管理者点了「调整岗位」就去开会，
# 学生一下午都报不了名——超时没保存就自动解冻并提醒他。
EDIT_LOCK_HOURS = 2

# 场次开始前多久自动推送签到提醒（管理者也可在签到面板手动补发）
CHECKIN_REMINDER_MINUTES = 30

# 生成签到码时默认的可签时段：场次起止各外扩这么多分钟。
# 卡着场次时间开闸的话，提前到岗的人签不了、拖堂散场的人也签不了，
# 现场只能改时段或补签。管理者仍可在生成时自行调整。
CHECKIN_WINDOW_PAD_MINUTES = 30

# 活动开始前多少天起，每天给管理者推一条人员变动摘要；几点推
ROSTER_DIGEST_DAYS = 3
ROSTER_DIGEST_HOUR = 6

# 学习材料单文件上限。文档自己存，视频走链接——自托管大视频要分片上传、
# 断点续传和对象存储，代价远超收益。
MAX_MATERIAL_BYTES = 50 * 1024 * 1024

# 报名问卷的文件题：一个人在一个活动里最多存着几份没提交的附件、总共多大。
# 传了不提交的文件不会自己消失，不设上限就是一条把磁盘写满的路。
MAX_PENDING_ATTACHMENTS = 5
MAX_PENDING_ATTACHMENT_BYTES = 50 * 1024 * 1024
# 没有被任何答案引用的附件留这么久就清掉：填到一半关掉页面很常见，
# 给足重来的时间，但不能永远留着。
ORPHAN_ATTACHMENT_DAYS = 7

# 活动类型：决定志愿者怎么进来。
#   instant  即选即得——报名即生效（或按名额进候补），场次自己决定选不选岗位；
#   screened 报名后筛选——志愿者填一份报名问卷并勾意向场次，报名截止后由管理者
#            挑人、定场次与岗位；申请不限人数，未录取会收到通知。
RECRUIT_MODES = {
    "instant": {"name": "即选即得",
                "desc": "报名即生效，名额已满自动进入候补"},
    "screened": {"name": "报名后筛选",
                 "desc": "志愿者填写报名问卷，报名截止后由管理者筛选并安排岗位"},
}


def recruit_name(code: str) -> str:
    return RECRUIT_MODES.get(code, {}).get("name", code)


# 学院清单（演示用，覆盖主要书院/学院）
COLLEGES = [
    "明理书院", "新闻学院", "法学院", "财政金融学院", "经济学院",
    "信息学院", "外国语学院", "马克思主义学院", "社会学院", "商学院",
    "统计学院", "劳动人事学院",
]

# 校区与学段。招募范围、场次与岗位的条件要求都从这两张表里选，
# 志愿者档案里也填这两项——两边取值必须同源，否则条件永远匹配不上。
CAMPUSES = ["中关村校区", "通州校区", "苏州校区"]
STAGES = ["本科生", "硕士研究生", "博士研究生"]
