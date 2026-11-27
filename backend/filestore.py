"""上传文件的物理存储：按内容去重；没人用满保留期、且库里登记过的文件才删。

磁盘上的文件名是「内容的 SHA-256 + 扩展名」，内容相同的上传只存一份，各条
Attachment 的 stored_name 指向同一个文件。复制岗位、复制活动也只是新建记录
指向原文件。权限跟着 Attachment 走，下载鉴权不看物理文件是谁传的。

指纹用 SHA-256 不用 MD5：MD5 能人为造出指纹相同、内容不同的两个文件，有人
抢先传一个伪造的，之后别人传的正常文件就会被指到它上面。

上传总是完整传到服务器、由服务器算指纹，不做「先报指纹、有就免传」：那样
别人能试探某个文件在不在平台上。接口返回里也不带「是否命中」。

**引用只有 Attachment.stored_name 一处。** 别的表要挂文件，存 Attachment 的
编号，不要另存文件名；否则这里数不到那份引用，文件会被当成没人用删掉。

删文件分两步：
  1. 删记录的事务提交后（release），另开会话数引用，数到 0 就在 StoredFile 上
     记下 orphaned_at，文件不动。
  2. 定时任务（sweep）只看 StoredFile 里 orphaned_at 早于 config.FILE_KEEP_DAYS
     天前的那几行，逐个再数一遍，仍是 0 才删文件和那一行。
保留期里文件一直在：哪处代码删错了记录，还找得回来。只删 StoredFile 登记过的
文件：数据库连错（连到空库、测试库）时那里一行也没有，什么都不会删。不扫目录。

并发：同一个文件名的「存入 / 共用」和「数引用」用一把事务级咨询锁串起来
（locks.file_lock）。store / share 在调用方的会话上拿锁，一直拿到调用方提交或
回滚；数引用的一方拿到锁之后再数，正在共用这个文件的上传要么已经提交（数得到），
要么还没进锁（进锁时会把 orphaned_at 清掉、文件不在就重新写一份）。
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import event, text
from sqlalchemy.orm import Session as OrmSession

from . import config
from .database import SessionLocal
from .locks import file_lock

logger = logging.getLogger(__name__)

_NEW = "filestore_new"        # 本事务存入的文件名：回滚后要数一遍
_DROP = "filestore_drop"      # 本事务删掉引用的文件名：提交后要数一遍

# 登记或重新启用一个文件：有人在用，orphaned_at 清空
_IN_USE = text(
    "INSERT INTO stored_files (name, size, created_at, orphaned_at) "
    "VALUES (:n, :s, :t, NULL) "
    "ON CONFLICT (name) DO UPDATE SET orphaned_at = NULL")
# 没人用了：记下从什么时候起（已经记过的不改，保留期从第一次没人用算）
_UNUSED = text(
    "INSERT INTO stored_files (name, size, created_at, orphaned_at) "
    "VALUES (:n, :s, :t, :t) "
    "ON CONFLICT (name) DO UPDATE SET orphaned_at = "
    "COALESCE(stored_files.orphaned_at, EXCLUDED.orphaned_at)")


def _now():
    from .models import now
    return now()


def digest_name(data: bytes, ext: str) -> str:
    return f"{hashlib.sha256(data).hexdigest()}.{ext.lower()}"


def _safe(name: str) -> bool:
    """只认 MATERIAL_DIR 下一层的普通文件名，拼不出目录外的路径。"""
    return (bool(name) and "/" not in name and "\\" not in name
            and not name.startswith("."))


def _write_atomic(name: str, data: bytes) -> None:
    """先写临时文件再改名：写到一半断电，不会留下名字对、内容残缺的文件。"""
    config.MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.MATERIAL_DIR / f".tmp-{uuid4().hex}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, config.MATERIAL_DIR / name)
    finally:
        tmp.unlink(missing_ok=True)


def _store_locked(db: OrmSession, data: bytes, name: str) -> str:
    path = config.MATERIAL_DIR / name
    # 大小对不上说明文件坏了（比如被截断），用这次的内容覆盖：名字就是内容的
    # 指纹，写回去的一定是对的内容
    if not path.is_file() or path.stat().st_size != len(data):
        _write_atomic(name, data)
    db.execute(_IN_USE, {"n": name, "s": len(data), "t": _now()})
    db.info.setdefault(_NEW, set()).add(name)
    return name


def store(db: OrmSession, data: bytes, ext: str) -> str:
    """存一份上传的内容，返回 stored_name。调用方在同一个事务里写 Attachment。"""
    name = digest_name(data, ext)
    file_lock(db, name)
    return _store_locked(db, data, name)


def store_many(db: OrmSession, items: list[tuple[bytes, str]]) -> list[str]:
    """一次存几份：先按文件名排好序再拿锁，两个请求各传同样几份时不会互相等成死锁。"""
    names = [digest_name(d, e) for d, e in items]
    for name in sorted(set(names)):
        file_lock(db, name)
    return [_store_locked(db, d, n) for (d, _e), n in zip(items, names)]


def share(db: OrmSession, name: str) -> bool:
    """准备再加一条指向 name 的记录（复制材料）。文件还在返回 True。

    锁拿到调用方提交为止：这期间别的会话删掉最后一条原记录、或者定时清理
    轮到它，都要等新记录提交后再数，不会把文件删掉。
    """
    if not _safe(name):
        return False
    file_lock(db, name)
    path = config.MATERIAL_DIR / name
    if not path.is_file():
        return False
    db.execute(_IN_USE, {"n": name, "s": path.stat().st_size, "t": _now()})
    return True


def release(db: OrmSession, name: str) -> None:
    """调用方在这个事务里删掉了一条指向 name 的记录。提交后没人用了就开始计保留期。"""
    if _safe(name):
        db.info.setdefault(_DROP, set()).add(name)


def drop_attachments(db: OrmSession, rows) -> int:
    """删掉这些附件记录，文件交给保留期和定时清理。不提交事务。"""
    n = 0
    for row in rows:
        if row.kind == "file":
            release(db, row.stored_name)
        db.delete(row)
        n += 1
    return n


def ref_count(db: OrmSession, name: str) -> int:
    return db.execute(text("SELECT count(*) FROM attachments WHERE stored_name = :n"),
                      {"n": name}).scalar_one()


def _mark_unused(names) -> None:
    """逐个文件拿锁、数引用，是 0 就记下 orphaned_at。每个文件一个短事务。"""
    for name in sorted(names):
        if not _safe(name):
            continue
        s = SessionLocal()
        try:
            file_lock(s, name)
            if ref_count(s, name) == 0:
                path = config.MATERIAL_DIR / name
                if path.is_file():
                    s.execute(_UNUSED, {"n": name, "s": path.stat().st_size,
                                        "t": _now()})
                else:
                    s.execute(text("DELETE FROM stored_files WHERE name = :n"),
                              {"n": name})
            s.commit()
        except Exception:  # noqa: BLE001  记不上只是这个文件多留着，不能让已提交的业务变成 500
            logger.exception("登记没人用的文件 %s 失败", name)
            s.rollback()
        finally:
            s.close()


def sweep(db: OrmSession) -> int:
    """定时任务：删掉没人用满 config.FILE_KEEP_DAYS 天的文件。返回删了几个。

    只按 StoredFile 的 orphaned_at 索引取那几行，不扫目录、不碰没登记的文件。
    每个文件拿锁后再数一遍引用：期间又有人用上了的，清掉 orphaned_at 留着。
    """
    cutoff = _now() - timedelta(days=config.FILE_KEEP_DAYS)
    names = [n for (n,) in db.execute(
        text("SELECT name FROM stored_files WHERE orphaned_at <= :c"),
        {"c": cutoff}).all()]
    db.commit()
    removed = 0
    for name in names:
        s = SessionLocal()
        try:
            file_lock(s, name)
            if ref_count(s, name):
                s.execute(text("UPDATE stored_files SET orphaned_at = NULL "
                               "WHERE name = :n"), {"n": name})
            else:
                if _safe(name):
                    path = config.MATERIAL_DIR / name
                    if path.is_file():
                        path.unlink()
                        removed += 1
                s.execute(text("DELETE FROM stored_files WHERE name = :n "
                               "AND orphaned_at <= :c"), {"n": name, "c": cutoff})
            s.commit()
        except Exception:  # noqa: BLE001  一个文件删不掉不该挡住后面的
            logger.exception("清理文件 %s 失败", name)
            s.rollback()
        finally:
            s.close()
    if removed:
        logger.info("清理没人用满 %d 天的文件 %d 个", config.FILE_KEEP_DAYS, removed)
    return removed


@event.listens_for(SessionLocal, "after_commit")
def _after_commit(session) -> None:
    session.info.pop(_NEW, None)
    names = session.info.pop(_DROP, None)
    if names:
        _mark_unused(names)


@event.listens_for(SessionLocal, "after_rollback")
def _after_rollback(session) -> None:
    # 回滚时登记的 StoredFile 也一起没了：新写的文件没人用，按没人用登记回去，
    # 不然它就成了库里没登记、永远不会被清理的文件
    session.info.pop(_DROP, None)
    names = session.info.pop(_NEW, None)
    if names:
        _mark_unused(names)
