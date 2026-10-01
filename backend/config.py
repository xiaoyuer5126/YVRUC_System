"""平台全局配置。

运行方式统一为 Docker Compose（docker-compose.yml）：
后端连容器内的 PostgreSQL 与 Redis，两者都是硬依赖，连不上直接启动失败——
半可用的服务比起不来更难排查。

与外部系统（消息中心 / 统一身份认证）的对接参数不写死在这里，
存数据库 integration_configs 表，管理员可在「集成设置」页在线修改。
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# —— 基础设施 ——
DATABASE_URL = os.environ["RUCVA_DATABASE_URL"]
REDIS_URL = os.environ["RUCVA_REDIS_URL"]

# —— 会话 ——
SESSION_HOURS = 24          # 令牌有效期；接 SSO 后由统一身份认证接管

# OAuth state 有效期：一次一用，够走完「跳去 CAS → 输密码（可能还有二次验证）
# → 跳回来」即可。给太短会在动作慢的人身上莫名其妙失败，给太长只是白白拉长
# 重放窗口。
SSO_STATE_SECONDS = 600

# 身份证号在库里是加密存的（见 crypto.py），密钥必须与数据库分开保管。
ID_CARD_KEY = os.environ.get("RUCVA_ID_CARD_KEY", "").strip()

# —— 演示登录 ——
# 仅在 SSO 未接通时开放，且必须配置共享口令；未配置则整个演示登录接口不存在。
# 学校 SSO 接通后删除 routers/auth.py 里的 demo 分支与这一项。
DEMO_LOGIN_PASSWORD = os.environ.get("RUCVA_DEMO_PASSWORD", "").strip()

# 接口文档（/docs、/openapi.json）仅在显式打开时暴露
EXPOSE_API_DOCS = os.environ.get("RUCVA_EXPOSE_DOCS", "").strip() == "1"

# —— 学校开放平台（能力开放平台 / 统一认证）——
# 在应用中心注册应用后获得，调用任何接口都要拿它俩签名。放环境变量而不是
# integration_configs 表：密钥不该出现在「集成设置」页的接口响应里。
# 留空 = 相关能力一律回落 Mock，测试环境天然拿不到这些变量。
OPENPLATFORM_APPID = os.environ.get("RUCVA_OPENPLATFORM_APPID", "").strip()
OPENPLATFORM_SECRET = os.environ.get("RUCVA_OPENPLATFORM_SECRET", "").strip()
OPENPLATFORM_GATEWAY = os.environ.get("RUCVA_OPENPLATFORM_GATEWAY", "").strip()
# 统一身份认证（CAS 与 OAuth 两套接口都挂在它下面）
CAS_BASE_URL = os.environ.get("RUCVA_CAS_BASE_URL", "").strip()
# OAuth 回调地址，必须与应用中心登记的**一字不差**，否则换令牌时报 invalid_grant。
# 单独一项而不是从 PUBLIC_BASE_URLS 里取：登记的是哪个地址只有学校后台说了算，
# 而且随时可能改，改这个不该顺带把二维码域名白名单也改了。
SSO_REDIRECT_URI = os.environ.get("RUCVA_SSO_REDIRECT_URI", "").strip()

# —— 地图（管理端看签到点与范围）——
# 瓦片由**我们自己的服务器**向天地图取、再转给浏览器（见 routers/geo.py），
# 前端拿不到任何 tk。不让浏览器直连天地图：那要靠 Referer 域名白名单保护浏览器端
# tk，而 AdGuard 隐身模式之类的插件会改写或去掉第三方 Referer，天地图一律 403，
# 地图整块灰掉——这不是用户能自己排查出来的。
#
# 天地图的**服务端** tk：瓦片转发和地名搜索都用它。天地图按**服务器出口 IP
# 白名单**校验它，所以要在天地图后台把服务器的公网 IP 登记上。它只在后端用，
# 绝不能进前端——会被抄走、额度算我们的。没配 = 管理端不画地图，只画点和圈，
# 定位签到功能照旧（那个本来就不依赖地图）。
# 浏览器端 tk 用不上：天地图对它只认真浏览器，服务器发出去的请求带什么
# Referer 都被拒（403 / 301012），所以不设浏览器端 tk 的配置。
TIANDITU_TK_SERVER = os.environ.get("RUCVA_TIANDITU_TK_SERVER", "").strip()

# 天地图每个 tk 每天 10000 次瓦片请求。保护有三层（见 routers/geo.py）：
# 同一张瓦片只向天地图取一次、在磁盘上存 TILE_CACHE_DAYS 天；Redis 按天数
# 真正打到天地图的请求，到 TIANDITU_WARN_AT 记警告日志；到上限直接 503，
# 不再打上游——超了天地图也是拒，还可能按违规封 tk（地名搜索用的是同一把），
# 不如自己先停。
TIANDITU_DAILY_LIMIT = int(os.environ.get("RUCVA_TIANDITU_DAILY_LIMIT", "").strip() or "10000")
TIANDITU_WARN_AT = TIANDITU_DAILY_LIMIT * 9 // 10
TILE_CACHE_DIR = DATA_DIR / "tiles"
TILE_CACHE_DAYS = 30
# 服务器向天地图取一张瓦片最多等几秒。给短：地图是锦上添花，一张瓦片卡住
# 就让它灰着，别占着线程池的线程。
TILE_UPSTREAM_TIMEOUT = 5
# 瓦片令牌的有效期（小时）。瓦片是 <img> 拉的，带不上登录令牌，所以管理端打开
# 地图时先领一张签名令牌挂在瓦片地址上；只防「拿我们的接口当免费瓦片源」，
# 不落库、不绑人。前端快过期时自己重新领。
TILE_TOKEN_HOURS = 6

# 校区的大致中心点：管理端「快捷选点」按它把地图挪过去、给个起点。
# 数值由用户提供（比网上查的可靠——不同来源能差四百米，比我们默认的 200 米
# 半径还大）。**它仍然不是签到点**：真正要定的是「东门」「某栋楼前」这种岗位
# 位置，所以按钮只负责把人和地图带到校区，落点还得拖着图钉挪到现场那一点上
# （见 CheckinPanel 的 geoWarn 与 set_geo 的审计）。
CAMPUS_PRESETS = [
    {"name": "中关村校区", "lat": 39.96900, "lng": 116.30741},
    {"name": "通州校区", "lat": 39.90511, "lng": 116.73993},
    {"name": "苏州校区", "lat": 31.27872, "lng": 120.72849},
]

# 平台对外网址（多个用逗号分隔），二维码里写的就是它。列在这里的域名才允许
# 出现在二维码中——否则任何人都能拿平台的接口生成一张指向自家站点的码，
# 扫的人只看到「志愿平台发出来的二维码」。留空则只认本次请求自己的域名。
def _url_list(raw: str) -> list[str]:
    """解析域名清单，JSON 数组与逗号分隔两种写法都认。

    两种都认是因为 .env 里真的出现过 JSON 写法，而按逗号切分会把方括号和
    引号留在值里——urlparse 解析出的 netloc 是空串，整个白名单静默失效，
    二维码照发不误、谁都没发现（见 qrcodes.py）。宁可爱吃两种格式，
    也不要一个不报错的错值。
    """
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        with contextlib.suppress(json.JSONDecodeError):
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                return [s for s in (str(x).strip().rstrip("/") for x in loaded) if s]
        # 半截 JSON（少了右括号之类）：退回按逗号切，但同样得把方括号和引号
        # 剥掉，否则切出来的 '["https://a.cn"' netloc 为空，白名单照样静默失效
        logging.getLogger(__name__).warning(
            "域名清单不是合法的 JSON 数组，已按逗号分隔解析：%s", raw)
    pieces = (s.strip().strip("[]\"' ").rstrip("/") for s in raw.split(","))
    return [s for s in pieces if s]


PUBLIC_BASE_URLS = _url_list(os.environ.get("RUCVA_PUBLIC_BASE_URL", ""))


def _site_root(raw: str) -> str:
    """站点根地址，只留「协议://域名[:端口][/路径]」。

    带上 # 或查询串的话，拼出来的通知链接就成了 https://x/#/my/activity/…
    这种认不出的地址，点开只会落到首页。不是 http(s) 网址的当作没配，
    记一条警告：发出去一个打不开的链接，不如不发。
    """
    raw = raw.strip()
    if not raw:
        return ""
    u = urlparse(raw)
    if u.scheme not in ("http", "https") or not u.netloc:
        logging.getLogger(__name__).warning("站点根地址不是 http(s) 网址，已忽略：%s", raw)
        return ""
    return f"{u.scheme}://{u.netloc}{u.path}".rstrip("/")


# 通知里「点击打开」的站点根地址，企业微信里的通知卡片点击后跳到它下面
# （落哪一页见 integrations/msgcenter._link_for）。必须是手机上打得开的
# 公网地址；没单独配就用正式域名的第一个，两个都空则通知不带跳转——
# 开发环境本来也没有公网入口。
SITE_URL = _site_root(os.environ.get("RUCVA_SITE_URL", "")
                      or (PUBLIC_BASE_URLS[0] if PUBLIC_BASE_URLS else ""))

# 下载票据：导出与材料下载走 <a> 直链，带不了请求头，改用一次性票据。
# 短到只够浏览器立刻发起这一次下载，进了日志也用不了第二次。
DOWNLOAD_TICKET_SECONDS = 60

# 后台定时任务循环（见 main._sweeper）。测试里要关掉：每建一个 TestClient
# 都会触发一次立即扫描，它在另一个线程里跟用例抢同一批数据——用例刚把编辑态
# 改成超时，扫描线程也看见了，于是解冻通知发了两条，断言随机失败。
RUN_SWEEPER = os.environ.get("RUCVA_DISABLE_SWEEPER", "").strip() != "1"

# —— 连接池 ——
# 每个进程各有一个池子，所以池子大小要乘上进程数一起算：Postgres 默认
# max_connections=100，按默认的 4 进程 4×(5+10)=60，留了余量。往上加进程
# （RUCVA_WORKERS）要顺手把这两个调小，否则峰值一连就顶到数据库上限，
# 表现是所有请求一起变慢甚至报错，而不是只慢一点。
DB_POOL_SIZE = int(os.environ.get("RUCVA_DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.environ.get("RUCVA_DB_MAX_OVERFLOW", "10"))

# —— 场次锁的进程内名额 ——
# 场次锁是在数据库里排队的（见 locks.shift_lock），排队期间占着一条连接。
# 每个进程最多放这么多个请求进数据库排队，其余的在进程里等、不占连接。
# 必须明显小于 DB_POOL_SIZE + DB_MAX_OVERFLOW（默认 15）：名额吃掉的连接越多，
# 留给刷页面、登录这些读请求的越少，抢报时整站跟着卡。锁本身一次只放一个人，
# 名额再多也不会更快，只是多几个人占着连接干等。
LOCK_SLOTS = int(os.environ.get("RUCVA_LOCK_SLOTS", "5"))

# —— 线程池 ——
# 同步的 def 接口都跑在线程池里，默认每个进程只有 40 条线程、所有接口共用。
# 抢报时拿到闸门号的报名请求要排队等场次锁（最多 TICKET_WAIT_TIMEOUT 秒），
# 排队期间占着一条线程；名额一多，刷页面的请求就得等线程。等锁的线程大多在
# 睡、不占 CPU；除了 LOCK_SLOTS 个在数据库里排队的，也不占数据库连接（进锁前
# 已还回池子），多开的代价只是内存。
THREADS = int(os.environ.get("RUCVA_THREADS", "100"))

# 已经扣到名额闸门号的报名请求，最多等场次锁多少秒。拿到号的人数不超过名额，
# 队伍不会无限长；等不到回 503 等于把名额让给后来重试的人，所以给得宽。
# 8 核机器实测 300 人抢 200 个名额，最慢的人要排 12～15 秒。
TICKET_WAIT_SECONDS = float(os.environ.get("RUCVA_TICKET_WAIT_SECONDS", "30"))

# —— 业务规则常量 ——
# 平台不对志愿者做任何限制：没有准入名单，也不拦任何人报名。临期退选只影响
# 那个名额（报名已截止时扣留不释放），并记一次「临期退选」供青协查看。
LATE_CANCEL_HOURS = 24     # 「临近」阈值（小时）

# 内部提示分（backend/scoring.py）。只给青协工作人员看，作用仅限于在名单上
# 挂一个感叹号。权重先按这套跑，回头由部长们定夺再改这里就行——分数是现算的，
# 改了立刻全平台生效，不会留下对不上的历史值。三项都按活动计，按时间顺序
# 逐条加减，每一步都夹在 [SCORE_MIN, SCORE_BASE]，满分时加的分不会存起来。
SCORE_BASE = 100            # 起始分，也是上限
SCORE_MIN = 0
SCORE_ALERT_BELOW = 80      # 低于它，名单上姓名旁挂感叹号
SCORE_PER_ACTIVITY = 5      # 每顺利参与一个活动（签到过、无违规、无临期退选）加的分
                            # 补记录活动没有签到：导入名单即视为参与（见 scoring）
LATE_CANCEL_WEIGHT = 5      # 一个活动有临期退选扣多少（退几个场次都只扣一次）
VIOLATION_WEIGHTS = {       # 一次违规扣多少，最重一档 15
    "late": 10,       # 迟到早退
    "leave": 15,      # 无故请假
    "passive": 10,    # 消极志愿
    "other": 5,       # 其他
}
PROMOTE_CONFIRM_HOURS = 2  # 候补转正确认窗口（小时），只算白天，见下
# 确认窗夜间不计时：[23:00, 次日 8:00) 这段不算进那 2 小时——半夜转正的人
# 睡一觉起来窗口就过了，记一次放弃不公平。起止是整点，要求 START > END（跨午夜）。
# 算法见 enroll_logic.confirm_deadline_from
PROMOTE_QUIET_START = 23
PROMOTE_QUIET_END = 8

# 同一个码扫两遍就是签到→签退，方便，但也意味着手滑扫两遍人就当场被签退了，
# 时长按 0 分钟算。所以签到后这么久之内，第二次扫码一律当作「重复操作」挡回去
# （见 routers/checkin.do_checkin）。散场时的正常签退都在这之后，不受影响。
CHECKOUT_MIN_GAP_MINUTES = 10

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
# 可签时段同时也是**签退**的时段：签退没有另外的宽限。码过了截止或被关掉时
# 还没签退的人，时长按场次结束时间兜底并标记待核（见 staff_ops.effective_checkout）。
CHECKIN_WINDOW_PAD_MINUTES = 30

# 动态签到码：管理端每 30 秒换一张，每张在 Redis 里活 60 秒。
# 换码不等于旧码立刻作废——留一倍时间给正在排队扫码的人（相机对焦慢、
# 手机卡一下），所以任意时刻最多两张码能用。截图转发出去，也就活这么久。
CHECKIN_CODE_REFRESH_SECONDS = 30
CHECKIN_CODE_TTL_SECONDS = 60

# 定位签到：管理者站在岗位上用手机量一个坐标存下来，志愿者到场后由平台比对
# 距离。它比动态码弱——手机定位能伪造，精度也看环境——所以只该用在「现场
# 根本没人能出示二维码」的岗位上（比如散在校园各处的值守点）。
# 默认半径 200 米，是「同一栋楼、同一个广场」的量级，管理员可按现场调整。
GEO_RADIUS_DEFAULT_M = 200
GEO_RADIUS_MIN_M = 30          # 再小就不是「人在现场」，是「贴着一个点站」
GEO_RADIUS_MAX_M = 3000

# 活动开始前多少天起，每天给管理者推一条人员变动摘要；几点推
ROSTER_DIGEST_DAYS = 3
ROSTER_DIGEST_HOUR = 6
# 值班日报：每晚这个点给启用值班的部门的部长发一份当天的审批汇总（本地时间）
DUTY_DIGEST_HOUR = 21
# 志愿北京名称被对外联络部退回、满这么多天管理者还没重提，每天这个点起
# 提醒一次本活动的管理者（本地时间）
VB_REMIND_DAYS = 3
VB_REMIND_HOUR = 9

# 学习材料单文件上限。文档自己存，视频走链接——自托管大视频要分片上传、
# 断点续传和对象存储，代价远超收益。
MAX_MATERIAL_BYTES = 50 * 1024 * 1024

# 意见反馈：正文字数、每条截图张数与单张大小、每人每小时条数。
# 截图比答疑群二维码（2MB）大：手机原图截屏动辄两三兆，卡在 2MB 大半传不上
FEEDBACK_MAX_CHARS = 2000
FEEDBACK_MAX_IMAGES = 3
FEEDBACK_MAX_IMAGE_BYTES = 5 * 1024 * 1024
FEEDBACK_PER_HOUR = 5

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
#   record   不公开招募（补记录）——线下已经办完的活动，只在平台上补记时长：
#            不上广场、不收报名、不建场次与岗位，照常过青协立项审批，通过后
#            由管理者导入「学号 + 时长」名单，之后走同一套时长结账流程。
RECRUIT_MODES = {
    "instant": {"name": "即选即得",
                "desc": "报名即生效，名额已满自动进入候补"},
    "screened": {"name": "报名后筛选",
                 "desc": "志愿者填写报名问卷，报名截止后由管理者筛选并安排岗位"},
    "record": {"name": "不公开招募（补记录）",
               "desc": "线下已组织过的活动，不公开、不报名、不建场次岗位；"
                       "审批通过后导入名单补记时长"},
}
RECORD_MODE = "record"
# 补记录名单的时长不设上限（多天的线下活动一个人可能记几十小时），超过这个数
# 照常导入，只在导入结果里标一句「请核对」——多半是把分钟当小时、多打了个 0
RECORD_WARN_HOURS = 24


def recruit_name(code: str) -> str:
    return RECRUIT_MODES.get(code, {}).get("name", code)


# 学院清单（演示用，覆盖主要书院/学院）
# 校区与学段。招募范围、场次与岗位的条件要求都从这两张表里选，
# 志愿者档案里也填这两项——两边取值必须同源，否则条件永远匹配不上。
CAMPUSES = ["中关村校区", "通州校区", "苏州校区"]
STAGES = ["本科生", "硕士研究生", "博士研究生"]

# 学号中间两位 → 学段。取值与 STAGES 同源，否则筛选条件永远匹配不上。
# 只列已确认的三种；留学生的中间两位可能与下表不同（尚未确认），
# 认不出的取值一律留空，不硬猜成某个学段——猜错的学段会让人被错误的
# 招募范围筛掉，而且没人看得出是哪里错了。
STAGE_BY_CODE = {
    "00": "博士研究生",
    "10": "硕士研究生",
    "20": "本科生",
}
