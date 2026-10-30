"""操作日志：谁、什么时候、从哪、调了哪个接口、结果如何。

和审计日志（audit.py）分工不同：审计日志是业务代码在关键动作上写的一句人话，
进数据库、对志愿者展示变更；操作日志是在请求入口统一采集的流水，覆盖所有改
数据的请求（POST/PUT/PATCH/DELETE）和登录、导出、附件下载，用来事后追溯。

存储是文件，不进数据库：日志和业务库分开放，库出了问题留痕还在，日志量也
不会拖慢业务表。格式是 JSON Lines，一行一条：
  · 每个进程每天一个文件 `YYYY-MM-DD-<pid>.jsonl`，只追加。几个进程各写各的，
    不需要跨进程加锁，也不做重命名轮转（轮转时别的进程还拿着旧文件）。
  · 请求线程只把一条记录放进内存队列，写盘由本进程的后台线程批量做——抢报
    时几百个报名请求同时到，谁都不该等磁盘；进程退出前由 close() 把队列写完。
  · 请求正文一律不记：报名问卷、个人资料里有身份证号、手机号。
  · 磁盘剩余空间低于 OPLOG_MIN_FREE_BYTES 时停写，站内通知管理员（每天一次）：
    日志把磁盘写满，数据库也就写不进去了。
  · 超过 OPLOG_RETAIN_DAYS 天的文件由定时任务删掉（scheduled.purge_oplogs）。
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import queue
import re
import shutil
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from itertools import groupby
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# 记了也没用的写请求：下载票据只是换一张一分钟的票，真正的下载另记一条
SKIP_HANDLERS = {"download_ticket"}

# 操作类型：页面上按它筛
KIND_LABELS = {"login": "登录", "download": "导出/下载", "create": "提交",
               "update": "修改", "delete": "删除"}
_METHOD_KIND = {"POST": "create", "PUT": "update", "PATCH": "update",
                "DELETE": "delete"}

# 请求线程往这里放、后台线程从这里取。上限防的是磁盘卡住时内存一直涨：
# 满了就丢新来的，并记一句警告
QUEUE_MAX = 20000
BATCH_MAX = 500
DISK_CHECK_SECONDS = 30

_queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
_write_lock = threading.Lock()
_writer: threading.Thread | None = None
_writer_pid = 0
_start_lock = threading.Lock()
_stopping = threading.Event()
_dropped = 0
_disk_ok = True
_disk_checked_at = 0.0

FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-\d+\.jsonl$")

# 当前请求的触发人。存的是可变 dict：同步依赖由 anyio 在线程池里跑，会拷贝一份
# 上下文，在依赖里 set 新值传不回处理函数；dict 是同一个对象，往里写谁都看得见。
_request_actor: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "oplog_request_actor", default=None)


# ======================= 采集 =======================

def mark_actor(request, user) -> None:
    """记下这次请求是谁发的。鉴权依赖在认出人之后调用。

    当场把学号、姓名、身份抄下来：请求结束时 ORM 对象可能已经脱离会话。
    """
    request.state.oplog_actor = {
        "uid": user.id, "sid": user.student_id, "name": user.name,
        "identity": user.view() or "",
    }
    actor = _request_actor.get()
    if actor is not None:
        actor["uid"] = user.id


def current_actor_id() -> str | None:
    """这次请求的触发人用户 id；定时任务等没有请求上下文时返回 None。"""
    actor = _request_actor.get()
    return actor.get("uid") if actor else None


def mark_login(request, *, student_id: str = "", user=None) -> None:
    """登录接口标出这次登录是谁。失败时没有 user，只记填的（或认证返回的）学号。"""
    request.state.oplog_login = True
    if user is not None:
        mark_actor(request, user)
    elif student_id:
        request.state.oplog_actor = {"uid": "", "sid": student_id[:32],
                                     "name": "", "identity": ""}


def mark_download(request) -> None:
    """直链下载（导出、附件）：GET 请求里只有这一类要记。"""
    request.state.oplog_download = True


def mark_note(request, text: str) -> None:
    """附一句说明：这次操作改成了什么值、选了哪一项。

    请求正文一律不记，所以「把调剂意愿改成了什么」这类信息得由处理函数
    自己报上来。只放一句人话，不进数据库的记录也不该塞大段内容。
    """
    request.state.oplog_note = text.strip()[:200]


# 操作日志里「操作」一列显示的名称。新增接口要在下面登记，没登记的就退回
# 处理函数第一行文档串、再没有就显示英文函数名（tests/test_oplog_labels.py 会拦）。
# 同名处理函数分属两个模块时，用「模块:函数名」指明。
LABELS = {
    'appeal_hours': '提出时长异议',
    'confirm_hours': '本人确认时长',
    'read_all': '通知全部已读',
    'ack_notification': '签收通知',
    'read_notification': '阅读通知',
    'update_profile': '完善/修改个人资料',
    'upload_project_file': '上传审批材料',
    'repush_vb': '重提志愿北京名称-再次',
    'resubmit_vb': '重提志愿北京名称-退回',
    'save_names': '预存志愿北京名称',
    'upload_doc_template': '上传/更换模板',
    'set_duty': '指定/交接值班',
    'decide_vb_confirm': '审批志愿北京名称',
    'download_material': '下载学习材料',
    'upload_material': '上传学习材料',
    'upload_notify_file': '上传通知附件',
    'add_link': '添加学习链接',
    'delete_material': '删除学习材料',
    'approvals:decide': '审批活动',
    'submit': '提交活动审批',
    'transfer': '转交活动审批',
    'create_department': '新增部门',
    'delete_department': '删除部门',
    'update_department': '修改部门',
    'put_announcement': '保存平台公告',
    'export_notifications': '导出通知记录',
    'export_oplogs': '导出操作日志',
    'scan': '扫码领取/归还',
    'ensure_tasks': '生成领取/归还码',
    'attire_batch': '修改服装领取情况',
    'attire_remind': '提醒领取/归还',
    'cancel_enroll': '取消报名',
    'confirm_promotion': '确认候补转正',
    'set_excuse': '培训/彩排请假',
    'set_reassign_pref': '修改调剂意愿',
    'enroll': '提交报名/候补',
    'submit_feedback': '提交问题反馈',
    'feedback_file': '查看反馈截图',
    'handle_feedback': '处理问题反馈',
    'apply': '提交报名申请',
    'withdraw': '撤回报名申请',
    'upload_answer_file': '上传文件',
    'screening:decide': '提交录取结果',
    'save_form': '保存报名问卷',
    'form_draft': '智能起草-报名问卷',
    'structure_draft': '智能起草-场次岗位',
    'activity_draft': '智能起草-新建活动',
    'submit_claim': '提交校外时长补登',
    'claim_file': '查看时长补登证明材料',
    'withdraw_claim': '撤回校外时长补登',
    'export_claims': '导出校外时长补登',
    'decide_claim': '审批校外时长补登',
    'revoke_claim': '撤销通过校外时长补登',
    'create_activity': '新建活动',
    'update_activity': '修改活动基本信息',
    'delete_activity': '删除活动草稿',
    'close': '手动截止报名',
    'copy_activity_route': '复制活动',
    'start_editing': '进入编辑态',
    'finish': '手动结束活动',
    'add_activity_manager': '添加活动管理者',
    'remove_activity_manager': '移除活动管理者',
    'set_manager_duties': '设置活动管理者分工',
    'notify_volunteers': '通知志愿者',
    'pin': '置顶活动',
    'publish': '发布活动',
    'finish_editing': '退出编辑态',
    'reopen': '手动开放报名',
    'create_shift': '新建场次',
    'contact_qr': '查看联络群二维码',
    'set_leader': '设置岗位组长',
    'promote_manually': '手动候补转正',
    'remind_batch': '提醒签收通知',
    'update_position': '修改岗位',
    'delete_position': '删除岗位',
    'lock_position': '锁定岗位名单',
    'pause_position': '手动暂停/恢复岗位报名',
    'update_shift': '修改场次',
    'delete_shift': '删除场次',
    'add_walkin': '手动添加志愿者',
    'assign_apply': '应用排班结果',
    'assign_import': '导入排班',
    'assign_preview': '一键排班',
    'lock_shift': '锁定场次名单',
    'open_pick': '开放选岗',
    'pause_shift': '手动暂停/恢复场次报名',
    'create_position': '新建岗位',
    'copy_positions_route': '复制岗位',
    'upload_image': '上传联络群二维码',
    'demo_login': '登录（演示口令）',
    'logout': '退出登录',
    'sso_callback': '统一身份认证登录',
    'geo_checkin': '定位签到',
    'do_checkin': '扫码签到',
    'export_answers': '导出问卷结果',
    'export_answer_attachments': '导出问卷附件',
    'export_applications': '导出报名问卷结果',
    'export_application_attachments': '导出报名问卷附件',
    'export': '导出排班表',
    'export_hours': '导出时长记录',
    'hours_approve': '确认核准时长',
    'hours_confirm': '确认时长',
    'hours_lock_request': '手动提交时长审批',
    'hours_manual': '手动加/减时长',
    'hours_manual_delete': '撤销手动加/减时长',
    'request_unlock': '提交时长修改审批',
    'toggle_task': '修改签到码',
    'batch_op': '手动签到/签退',
    'set_geo': '设置定位签到',
    'clear_geo': '设置扫码签到',
    'resolve_appeal': '处理时长申诉',
    'decide_hours_request': '处理时长修改审批',
    'send_reminder': '手动提醒签到',
    'create_task': '生成签到码',
    'export_activities_summary': '导出活动汇总',
    'create_violation': '登记违规记录',
    'delete_violation': '撤销违规登记',
    'import_text': '补录活动确认导入时长-文字',
    'import_file': '补录活动确认导入时长-文件',
    'preview_file': '补录活动预览导入时长-文字',
    'preview_text': '补录活动预览导入时长-文件',
    'edit_record': '补录活动修改导入时长',
    'delete_record': '补录活动删除导入时长',
    'batch_roles': '批量修改角色',
    'set_roles': '修改角色',
}


def _label(endpoint) -> str:
    """接口的中文说明：名称字典里的登记，没登记的回落到处理函数的第一句文档串。"""
    name = getattr(endpoint, "__name__", "")
    module = (getattr(endpoint, "__module__", "") or "").rsplit(".", 1)[-1]
    key = f"{module}:{name}"
    if key in LABELS:
        return LABELS[key]
    if name in LABELS:
        return LABELS[name]
    doc = (getattr(endpoint, "__doc__", "") or "").strip()
    if not doc:
        return name
    first = doc.splitlines()[0].strip()
    return re.split(r"[。：:（(]", first, maxsplit=1)[0][:40] or first[:40]


class OplogMiddleware:
    """纯 ASGI 中间件：在响应发出后拼一条记录放进队列。

    不用 BaseHTTPMiddleware：那一层会把每个请求包进额外的任务和流，抢报时
    白白多出一份开销。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        at = datetime.now()
        status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        token = _request_actor.set({})
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                entry = _entry(scope, at, status["code"],
                               int((time.perf_counter() - started) * 1000))
                if entry is not None:
                    record(entry)
            except Exception:  # noqa: BLE001
                logger.exception("操作日志采集出错")
            finally:
                _request_actor.reset(token)


def _entry(scope, at: datetime, status: int, ms: int) -> dict | None:
    state = scope.get("state") or {}
    method = scope["method"]
    endpoint = scope.get("endpoint")
    handler = getattr(endpoint, "__name__", "")
    if state.get("oplog_login"):
        kind = "login"
    elif method in WRITE_METHODS:
        if handler in SKIP_HANDLERS:
            return None
        kind = _METHOD_KIND[method]
    elif state.get("oplog_download"):
        kind = "download"
    else:
        return None
    actor = state.get("oplog_actor") or {}
    params = dict(scope.get("path_params") or {})
    client = scope.get("client")
    return {
        "t": at.isoformat(timespec="milliseconds"),
        "uid": actor.get("uid", ""), "sid": actor.get("sid", ""),
        "name": actor.get("name", ""), "identity": actor.get("identity", ""),
        "ip": client[0] if client else "",
        "method": method, "path": scope["path"],
        "action": handler, "label": _label(endpoint) if endpoint else "",
        "kind": kind, "note": state.get("oplog_note", ""),
        "activity_id": str(params.pop("activity_id", "") or ""),
        "target": {k: str(v) for k, v in params.items()},
        "status": status, "ms": ms,
    }


def record(entry: dict) -> None:
    global _dropped
    _ensure_writer()
    try:
        _queue.put_nowait(entry)
    except queue.Full:
        _dropped += 1
        if _dropped % 1000 == 1:
            logger.warning("操作日志队列已满，已丢弃 %d 条", _dropped)


# ======================= 写盘 =======================

def _ensure_writer() -> None:
    """每个进程起一条写盘线程。按进程号判断，多进程部署各起各的。"""
    global _writer, _writer_pid
    pid = os.getpid()
    if _writer is not None and _writer_pid == pid and _writer.is_alive():
        return
    with _start_lock:
        if _writer is not None and _writer_pid == pid and _writer.is_alive():
            return
        _writer = threading.Thread(target=_run, name="oplog-writer", daemon=True)
        _writer_pid = pid
        _writer.start()


def _run() -> None:
    while True:
        try:
            # 平时阻塞等（不轮询）；close() 之后再空等一秒就退出
            first = _queue.get(timeout=1.0 if _stopping.is_set() else None)
        except queue.Empty:
            return
        batch = [first, *_drain(BATCH_MAX - 1)]
        try:
            _write(batch)
        except Exception:  # noqa: BLE001
            logger.exception("操作日志写盘出错")
        finally:
            _done(len(batch))


def _done(n: int) -> None:
    for _ in range(n):
        _queue.task_done()


def _drain(limit: int) -> list[dict]:
    out = []
    while len(out) < limit:
        try:
            out.append(_queue.get_nowait())
        except queue.Empty:
            break
    return out


def _write_pending() -> None:
    """把队列里还没写的记录当场写掉。"""
    while True:
        batch = _drain(BATCH_MAX)
        if not batch:
            break
        try:
            _write(batch)
        finally:
            _done(len(batch))


def flush() -> None:
    """写完队列里的记录，并等后台线程手上那一批也写完。

    只给测试用：线上请求不停地进队列，等「全部写完」可能一直等下去。
    """
    _write_pending()
    _queue.join()


def close() -> None:
    """进程退出前把队列里的记录写完（见 main.lifespan）。

    写盘线程是守护线程，进程退出不等它；这里由调用线程把队列里剩的写掉，
    它手上正写的那一批也落在同一把写锁里，会被等到。
    """
    _stopping.set()
    _write_pending()


def _write(batch: list[dict]) -> None:
    with _write_lock:
        if not _disk_has_room():
            return
        by_day: dict[str, list[str]] = {}
        for e in batch:
            by_day.setdefault(e["t"][:10], []).append(
                json.dumps(e, ensure_ascii=False))
        config.OPLOG_DIR.mkdir(parents=True, exist_ok=True)
        pid = os.getpid()
        for day, lines in by_day.items():
            with open(config.OPLOG_DIR / f"{day}-{pid}.jsonl", "a",
                      encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")


def _disk_has_room() -> bool:
    """剩余空间够不够。DISK_CHECK_SECONDS 内只查一次。"""
    global _disk_ok, _disk_checked_at
    t = time.monotonic()
    if t - _disk_checked_at < DISK_CHECK_SECONDS and _disk_checked_at:
        return _disk_ok
    _disk_checked_at = t
    config.OPLOG_DIR.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(config.OPLOG_DIR).free
    ok = free >= config.OPLOG_MIN_FREE_BYTES
    if not ok:
        logger.error("磁盘剩余 %.2f GB，低于下限，操作日志停写", free / 1024 ** 3)
        _alert_disk_low(free)
    _disk_ok = ok
    return ok


def _alert_disk_low(free: int) -> None:
    """站内通知全体管理员。几个进程都会走到这里，用 Redis 占位保证每天只发一次。"""
    from . import locks
    from .database import SessionLocal
    from .models import User
    from .notify import notify

    if not locks.claim_once(f"oplog:disk-alert:{date.today().isoformat()}",
                            2 * 86400):
        return
    db = SessionLocal()
    try:
        admins = (db.query(User)
                  .filter(User.roles.contains(["admin"])).all())
        gb = free / 1024 ** 3
        for u in admins:
            if u.has_role("admin", real=True):
                notify(db, u, "服务器磁盘空间不足",
                       f"服务器磁盘只剩 {gb:.2f} GB，操作日志已暂停记录。"
                       "请尽快清理磁盘，空间恢复后自动继续记录。")
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("通知管理员磁盘不足失败")
    finally:
        db.close()


# ======================= 清理 =======================

def purge(today: date) -> int:
    """删掉超过保留期的日志文件，返回删了几个。"""
    if not config.OPLOG_DIR.is_dir():
        return 0
    cutoff = (today - timedelta(days=config.OPLOG_RETAIN_DAYS)).isoformat()
    n = 0
    for path in config.OPLOG_DIR.iterdir():
        m = FILE_RE.match(path.name)
        if m and m.group(1) < cutoff:
            path.unlink(missing_ok=True)
            n += 1
    return n


# ======================= 读取 =======================

def _files_between(start: date, end: date) -> list[Path]:
    """窗口内的日志文件，按文件名（日期在前）排好。"""
    if not config.OPLOG_DIR.is_dir():
        return []
    lo, hi = start.isoformat(), end.isoformat()
    out = []
    for path in config.OPLOG_DIR.iterdir():
        m = FILE_RE.match(path.name)
        if m and lo <= m.group(1) <= hi:
            out.append(path)
    out.sort()
    return out


def _records(path: Path):
    """一个文件里的记录，按写入顺序。"""
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                yield json.loads(line)
            except ValueError:
                continue      # 进程被杀时可能留下半行


def iter_rows(start: datetime, end: datetime, *, person: str = "",
              activity_ids: set[str] | None = None, kind: str = ""):
    """逐天吐出 [start, end] 之间符合条件的记录，从早到晚。

    每条记的是请求开始的时刻，却要等请求结束才进队列写盘：慢请求排在比它晚开始
    的快请求后面，一个文件里的记录并不按时间排。所以一天的几个文件读完、筛完，
    在内存里按时间排好再吐；分页和导出都靠这个顺序，要是照写入顺序吐，翻页时
    会漏掉或重复记录。驻留的是一天里符合条件的记录，不是整个窗口。
    同一毫秒的几条按文件名、文件内行号排（排序是稳定的），每次读顺序都一样。
    person 匹配学号或姓名（包含即可）；activity_ids 为 None 表示不按活动筛。
    """
    # 刚发生的请求可能还在队列里，先写掉，查得到才像实时
    _write_pending()
    lo = start.isoformat(timespec="milliseconds")
    hi = end.isoformat(timespec="milliseconds")
    person = person.strip()
    by_day = groupby(_files_between(start.date(), end.date()),
                     key=lambda p: FILE_RE.match(p.name).group(1))
    for _day, paths in by_day:
        day_rows = []
        for path in paths:
            for e in _records(path):
                if not (lo <= e.get("t", "") <= hi):
                    continue
                if kind and e.get("kind") != kind:
                    continue
                if person and person not in e.get("sid", "") \
                        and person not in e.get("name", ""):
                    continue
                if activity_ids is not None \
                        and e.get("activity_id") not in activity_ids:
                    continue
                day_rows.append(e)
        day_rows.sort(key=lambda e: e.get("t", ""))
        yield from day_rows


def page_rows(start: datetime, end: datetime, *, page: int, size: int,
              person: str = "", activity_ids: set[str] | None = None,
              kind: str = "") -> tuple[list[dict], int]:
    """一页记录（新的在前）和符合条件的总条数，一趟扫描同时得出。

    iter_rows 从早到晚吐，定长 deque 兜住的最后 page*size 条就是最新的那一批；
    总数顺手数出来，不用为了分页再走一遍。
    """
    latest: deque[dict] = deque(maxlen=page * size)
    total = 0
    for e in iter_rows(start, end, person=person, activity_ids=activity_ids,
                       kind=kind):
        total += 1
        latest.append(e)
    rows = list(reversed(latest))
    begin = (page - 1) * size
    return rows[begin:begin + size], total
