"""上传文件的物理存储：按内容去重，引用数降到 0 才删。

磁盘上的文件名是「内容的 SHA-256 + 扩展名」，内容相同的上传只存一份，各条
Attachment 的 stored_name 指向同一个文件。复制岗位、复制活动也只是新建记录
指向原文件。权限跟着 Attachment 走，下载鉴权不看物理文件是谁传的。

指纹用 SHA-256 不用 MD5：MD5 能人为造出指纹相同、内容不同的两个文件，有人
抢先传一个伪造的，之后别人传的正常文件就会被指到它上面。

上传总是完整传到服务器、由服务器算指纹，不做「先报指纹、有就免传」：那样
别人能试探某个文件在不在平台上。接口返回里也不带「是否命中」。

**引用只有 Attachment.stored_name 一处。** 别的表要挂文件，存 Attachment 的
编号，不要另存文件名；否则这里数不到那份引用，删最后一条 Attachment 时会把
还在用的文件删掉。

并发：同一个文件名的「存入 / 共用」和「删除」用一把事务级咨询锁串起来
（locks.file_lock）。
  · store / share 在调用方的会话上拿锁，一直拿到调用方提交或回滚。这期间
    新记录还没提交，别的会话数不到它，所以删除方必须等这把锁。
  · release 只登记，等删掉记录的事务提交之后，另开一个会话拿锁、重新数一遍
    引用，确实是 0 才删文件。在提交之后数：删除方的事务回滚了，文件不会已经
    被删；拿到锁之后数：正在共用这个文件的上传要么已经提交（数得到），要么
    还没进锁（进锁时发现文件不在，会重新写一份）。
  · 存入文件之后事务回滚的，回滚后照样数一遍，没人用就删，不留孤儿文件。

没有按目录扫描、删掉「库里查不到的文件」的清理：数据库连错一次（连到空库、
测试库），那种清理会把整个卷删光。
"""
from __future__ import annotations

import hashlib
import logging
import os
from uuid import uuid4

from sqlalchemy import event, text
from sqlalchemy.orm import Session as OrmSession

from . import config
from .database import SessionLocal
from .locks import file_lock

logger = logging.getLogger(__name__)

_NEW = "filestore_new"        # 本事务存入的文件名：回滚后要数一遍
_DROP = "filestore_drop"      # 本事务删掉引用的文件名：提交后要数一遍


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

    锁拿到调用方提交为止：这期间别的会话删掉最后一条原记录，也要等新记录
    提交后再数，不会把文件删掉。
    """
    if not _safe(name):
        return False
    file_lock(db, name)
    return (config.MATERIAL_DIR / name).is_file()


def release(db: OrmSession, name: str) -> None:
    """调用方在这个事务里删掉了一条指向 name 的记录。提交后没有记录再指向它，就删文件。"""
    if _safe(name):
        db.info.setdefault(_DROP, set()).add(name)


def ref_count(db: OrmSession, name: str) -> int:
    return db.execute(text("SELECT count(*) FROM attachments WHERE stored_name = :n"),
                      {"n": name}).scalar_one()


def purge(names) -> int:
    """逐个文件拿锁、数引用，是 0 才删。返回删掉几个。每个文件一个短事务。"""
    n = 0
    for name in sorted(names):
        if not _safe(name):
            continue
        s = SessionLocal()
        try:
            file_lock(s, name)
            if ref_count(s, name) == 0:
                path = config.MATERIAL_DIR / name
                if path.is_file():
                    path.unlink()
                    n += 1
            s.commit()
        except Exception:  # noqa: BLE001  删不掉只是多留一个文件，不能让已提交的业务变成 500
            logger.exception("清理文件 %s 失败", name)
            s.rollback()
        finally:
            s.close()
    return n


@event.listens_for(SessionLocal, "after_commit")
def _purge_after_commit(session) -> None:
    session.info.pop(_NEW, None)
    names = session.info.pop(_DROP, None)
    if names:
        purge(names)


@event.listens_for(SessionLocal, "after_rollback")
def _purge_after_rollback(session) -> None:
    session.info.pop(_DROP, None)
    names = session.info.pop(_NEW, None)
    if names:
        purge(names)
