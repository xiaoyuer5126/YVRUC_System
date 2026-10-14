"""备份容器：每天定时备份数据库与文件卷，按「每日 / 每周 / 每月」保留，也能手动备份与恢复。

    python3 -m backup.job run                     # 常驻：每天 YVRUC_BACKUP_AT 备一次（容器默认命令）
    python3 -m backup.job now                     # 立即备一份（手动，单独保留）
    python3 -m backup.job list                    # 列出现有备份
    python3 -m backup.job restore <名字> --yes    # 用某一份覆盖数据库与文件卷

一份备份是 /backups 下的一个文件夹，名字形如 2026-10-12_0300_daily：
    db.dump              pg_dump 自定义格式（YVRUC_BACKUP_DB=0 时没有）
    files.tar.gz         用户上传的文件（卷是 yvruc_files，后端挂在
                         /app/data/files，这里挂在 /data/files，同一个卷）
    oplog.tar.gz         操作日志
    manifest.json        时间、种类、各文件大小
先写进 .tmp- 开头的文件夹，全部成功才改名，所以没改名的都是半截，下次启动时清掉。

只用标准库：镜像是 postgres 官方镜像加一个 python3，pg_dump 版本与数据库对齐。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

BACKUP_ROOT = Path(os.environ.get("YVRUC_BACKUP_ROOT", "/backups"))
DATA_ROOT = Path(os.environ.get("YVRUC_BACKUP_DATA_ROOT", "/data"))
# 要备的文件卷，名字就是在容器里挂的目录名（见 docker-compose.yml 的 backup 服务）。
# 上传件那一个叫 files：代码里的目录名（config.MATERIAL_DIR）、卷名（yvruc_files）、
# 备份包里的文件名（files.tar.gz）处处一致
VOLUMES = ("files", "oplog")

KEEP_DAILY = 7
KEEP_WEEKLY = 4
KEEP_MONTHLY = 6
# 手动备份多在部署、改表前跑，留着是为了出事能退回去，不参与每日轮换
KEEP_MANUAL = 10
# 定时备份失败后隔多久再试：失败多半是数据库没起来，马上重试只会刷日志
RETRY_AFTER = timedelta(minutes=10)
# 启动后先等一会再看要不要补备：开机时各容器一起起来，数据库还没准备好
STARTUP_DELAY = 60

NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{4})_(daily|manual)$")
STAMP_FMT = "%Y-%m-%d_%H%M"
TMP_PREFIX = ".tmp-"


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


# ---------- 保留策略（纯函数，测试直接调） ----------

def keep_set(stamps: list[datetime], daily: int = KEEP_DAILY, weekly: int = KEEP_WEEKLY,
             monthly: int = KEEP_MONTHLY) -> set[datetime]:
    """定时备份里该留下的那些。

    三档各自从新到旧数：每天留当天最后一份，留够 daily 天；每周（周一到周日）留这周
    最后一份，留够 weekly 周；每月留这个月最后一份，留够 monthly 个月。三档取并集。
    按「有备份的天/周/月」计数而不是按日历往回推：电脑关机漏掉几天，也照样留够份数，
    不会因为漏了周日那一份就整周没有。
    """
    ordered = sorted(set(stamps), reverse=True)
    keep: set[datetime] = set()
    buckets = (
        (daily, lambda t: t.date()),
        (weekly, lambda t: tuple(t.isocalendar())[:2]),
        (monthly, lambda t: (t.year, t.month)),
    )
    for limit, bucket in buckets:
        seen: set = set()
        for t in ordered:
            key = bucket(t)
            if key in seen:
                continue
            if len(seen) >= limit:
                break
            seen.add(key)
            keep.add(t)
    return keep


def is_due(now: datetime, daily_dates: set[date], at: dtime) -> bool:
    """今天过了备份时刻、还没有今天的定时备份就该备。

    电脑在备份时刻关着机的话，开机后容器起来当天就补一份。
    """
    return now.time() >= at and now.date() not in daily_dates


def parse_at(text: str) -> dtime:
    hour, minute = text.strip().split(":")
    return dtime(int(hour), int(minute))


# ---------- 目录里的备份 ----------

def list_backups(root: Path = BACKUP_ROOT) -> list[tuple[str, datetime, str]]:
    """(名字, 时间, 种类)，从旧到新。不认识的文件夹不管：那可能是用户自己放的。"""
    found = []
    if not root.is_dir():
        return found
    for p in root.iterdir():
        m = NAME_RE.match(p.name)
        if p.is_dir() and m:
            found.append((p.name, datetime.strptime(m.group(1), STAMP_FMT), m.group(2)))
    return sorted(found, key=lambda x: (x[1], x[0]))


def prune(root: Path = BACKUP_ROOT) -> list[str]:
    """按保留策略删掉多余的备份，返回删掉的名字。"""
    backups = list_backups(root)
    daily = [t for _, t, kind in backups if kind == "daily"]
    keep_daily = keep_set(daily)
    manual = [name for name, _, kind in backups if kind == "manual"]
    keep_manual = set(manual[-KEEP_MANUAL:])
    removed = []
    for name, t, kind in backups:
        if (kind == "daily" and t not in keep_daily) or (kind == "manual" and name not in keep_manual):
            shutil.rmtree(root / name)
            removed.append(name)
    return removed


def clean_partial(root: Path = BACKUP_ROOT) -> None:
    if not root.is_dir():
        return
    for p in root.iterdir():
        if p.name.startswith(TMP_PREFIX):
            shutil.rmtree(p, ignore_errors=True)
            log(f"清掉上次没做完的 {p.name}")


# ---------- 备份 ----------

def db_enabled() -> bool:
    return os.environ.get("YVRUC_BACKUP_DB", "1").strip() not in ("0", "", "false", "no")


def libpq_url() -> str:
    """后端的连接串是 SQLAlchemy 写法（postgresql+psycopg://），pg_dump 只认 postgresql://。"""
    url = os.environ.get("YVRUC_DATABASE_URL", "")
    if not url:
        raise SystemExit("没有 YVRUC_DATABASE_URL，导不了数据库（不备数据库就设 YVRUC_BACKUP_DB=0）")
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


def _run(cmd: list[str]) -> None:
    # 连接串里带口令，出错时只报命令名，不把整条命令打进日志
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} 失败：{res.stderr.strip()}")


def archive_volume(src: Path, dest: Path) -> None:
    with tarfile.open(dest, "w:gz") as tar:
        for child in sorted(src.iterdir()):
            tar.add(child, arcname=child.name)


def give_to_owner(path: Path, root: Path) -> None:
    """备份交给备份文件夹的属主。

    容器里是 root 在写，Linux 上不改属主的话，部署的人不用 sudo 就打不开自己的备份。
    Windows 的挂载目录没有这层权限，chown 什么也不改。
    """
    st = root.stat()
    if st.st_uid == 0:
        return
    for p in [path, *path.rglob("*")]:
        os.chown(p, st.st_uid, st.st_gid, follow_symlinks=False)


def make_backup(kind: str, root: Path = BACKUP_ROOT, now: datetime | None = None,
                with_db: bool | None = None) -> Path:
    now = now or datetime.now()
    with_db = db_enabled() if with_db is None else with_db
    name = f"{now.strftime(STAMP_FMT)}_{kind}"
    final = root / name
    if final.exists():
        # 同一分钟里第二次手动备份：等到下一分钟，免得覆盖刚备好的那份
        raise RuntimeError(f"{name} 已经存在，过一分钟再备")
    tmp = root / f"{TMP_PREFIX}{name}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    started = time.monotonic()
    try:
        files = {}
        if with_db:
            _run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges",
                  "--file", str(tmp / "db.dump"), "--dbname", libpq_url()])
            files["db.dump"] = (tmp / "db.dump").stat().st_size
        for vol in VOLUMES:
            src = DATA_ROOT / vol
            if not src.is_dir():
                raise RuntimeError(f"文件卷 {vol} 没挂进来（应在 {src}）")
            archive_volume(src, tmp / f"{vol}.tar.gz")
            files[f"{vol}.tar.gz"] = (tmp / f"{vol}.tar.gz").stat().st_size
        manifest = {"name": name, "kind": kind, "created_at": now.isoformat(timespec="seconds"),
                    "database": with_db, "files": files}
        (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        give_to_owner(tmp, root)
        tmp.rename(final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    total = sum(files.values())
    log(f"备份完成 {name}：{'含数据库，' if with_db else '不含数据库，'}"
        f"共 {total / 1024 / 1024:.1f} MB，用时 {time.monotonic() - started:.0f} 秒")
    for gone in prune(root):
        log(f"按保留策略删掉 {gone}")
    return final


# ---------- 恢复 ----------

def _other_connections(url: str) -> int:
    res = subprocess.run(
        ["psql", "--dbname", url, "--tuples-only", "--no-align", "--command",
         "select count(*) from pg_stat_activity "
         "where datname = current_database() and pid <> pg_backend_pid()"],
        capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"连不上数据库：{res.stderr.strip()}")
    return int(res.stdout.strip() or 0)


def restore_volume(archive: Path, dest: Path) -> None:
    """清空卷再解包：备份之后新增的文件也要去掉，不然恢复出来的不是那个时刻的样子。"""
    for child in dest.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(dest, filter="data")


def restore(name: str, only: str | None, root: Path = BACKUP_ROOT) -> None:
    src = root / name
    if not NAME_RE.match(name) or not (src / "manifest.json").is_file():
        raise SystemExit(f"找不到备份 {name}，用 list 看有哪些")
    if only in (None, "db"):
        dump = src / "db.dump"
        if not dump.is_file():
            if only == "db":
                raise SystemExit(f"{name} 里没有数据库导出")
            log(f"{name} 不含数据库，只恢复文件卷")
        else:
            url = libpq_url()
            busy = _other_connections(url)
            if busy:
                raise SystemExit(f"数据库上还有 {busy} 条别的连接。先 docker compose stop backend 再恢复")
            _run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges",
                  "--single-transaction", "--exit-on-error", "--dbname", url, str(dump)])
            log("数据库已恢复")
    if only in (None, "files"):
        for vol in VOLUMES:
            archive = src / f"{vol}.tar.gz"
            dest = DATA_ROOT / vol
            if not archive.is_file():
                log(f"{name} 里没有 {vol}，跳过")
                continue
            if not dest.is_dir():
                raise SystemExit(f"文件卷 {vol} 没挂进来（应在 {dest}）")
            restore_volume(archive, dest)
            log(f"文件卷 {vol} 已恢复")


# ---------- 入口 ----------

def run_forever() -> None:
    at = parse_at(os.environ.get("YVRUC_BACKUP_AT", "03:00"))
    log(f"备份服务启动：每天 {at:%H:%M}，{'含' if db_enabled() else '不含'}数据库，"
        f"保留 {KEEP_DAILY} 天 / {KEEP_WEEKLY} 周 / {KEEP_MONTHLY} 月，手动备份留最近 {KEEP_MANUAL} 份")
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    clean_partial()
    time.sleep(STARTUP_DELAY)
    last_fail: datetime | None = None
    while True:
        now = datetime.now()
        done = {t.date() for _, t, kind in list_backups() if kind == "daily"}
        if is_due(now, done, at) and (last_fail is None or now - last_fail >= RETRY_AFTER):
            try:
                make_backup("daily", now=now)
                last_fail = None
            except Exception as exc:  # 常驻进程不能因为一次失败退出
                last_fail = now
                log(f"备份失败，{int(RETRY_AFTER.total_seconds() // 60)} 分钟后重试：{exc}")
        time.sleep(30)


def main(argv: list[str] | None = None) -> None:
    # 容器里 python 是 1 号进程，不装处理函数就收不到 docker stop 的 SIGTERM
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    os.umask(0o077)  # 备份里有姓名、学号、手机号，Linux 上只给属主读（属主见 give_to_owner）
    parser = argparse.ArgumentParser(prog="backup")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="常驻，每天定时备份")
    sub.add_parser("now", help="立即备份一份")
    sub.add_parser("list", help="列出现有备份")
    p_restore = sub.add_parser("restore", help="用一份备份覆盖数据库与文件卷")
    p_restore.add_argument("name")
    p_restore.add_argument("--only", choices=["db", "files"])
    p_restore.add_argument("--yes", action="store_true", help="确认覆盖")
    args = parser.parse_args(argv)

    if args.cmd == "run":
        run_forever()
    elif args.cmd == "now":
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            make_backup("manual")
        except RuntimeError as exc:
            raise SystemExit(f"备份失败：{exc}")
    elif args.cmd == "list":
        for name, _, _ in list_backups():
            files = json.loads((BACKUP_ROOT / name / "manifest.json").read_text())["files"]
            print(f"{name}  {sum(files.values()) / 1024 / 1024:8.1f} MB  {' '.join(files)}")
    elif args.cmd == "restore":
        what = {"db": "数据库", "files": "文件卷"}.get(args.only, "数据库和文件卷")
        if not args.yes:
            raise SystemExit(f"这会用 {args.name} 覆盖现在的{what}，之后的改动全部丢失。确认就加 --yes 再跑一次")
        try:
            restore(args.name, args.only)
        except RuntimeError as exc:
            raise SystemExit(f"恢复失败：{exc}")


if __name__ == "__main__":
    main()
