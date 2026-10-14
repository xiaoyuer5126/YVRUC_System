"""备份容器（backup/job.py）：保留策略、定时判断、文件卷的备份与恢复。

数据库那一步要 pg_dump，这里不测；它在本地 compose 上实际跑过一遍备份和恢复。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from backup import job


def _days(start: datetime, n: int) -> list[datetime]:
    return [start + timedelta(days=i) for i in range(n)]


def test_每天备一份时留7天_4周_6月():
    # 2026-01-01 起每天 03:00 一份，备到 2026-10-11（周日）
    stamps = _days(datetime(2026, 1, 1, 3, 0), 284)
    assert stamps[-1].date() == date(2026, 10, 11)
    keep = job.keep_set(stamps)
    kept = sorted(t.date() for t in keep)
    daily = [date(2026, 10, d) for d in range(5, 12)]
    weekly = [date(2026, 9, 20), date(2026, 9, 27), date(2026, 10, 4), date(2026, 10, 11)]
    monthly = [date(2026, 5, 31), date(2026, 6, 30), date(2026, 7, 31),
               date(2026, 8, 31), date(2026, 9, 30), date(2026, 10, 11)]
    assert kept == sorted(set(daily + weekly + monthly))


def test_关机漏掉的日子不占名额():
    # 只有隔天才有备份：每日一档仍留够 7 份，往回多覆盖几天
    stamps = _days(datetime(2026, 9, 1, 3, 0), 41)[::2]
    keep = job.keep_set(stamps, weekly=0, monthly=0)
    assert sorted(keep) == sorted(stamps)[-7:]


def test_漏了周日那一份这周仍留一份():
    # 2026-10-11 是周日，这天没备；这周留周六那份
    stamps = [t for t in _days(datetime(2026, 9, 1, 3, 0), 42) if t.date() != date(2026, 10, 11)]
    keep = job.keep_set(stamps, daily=0, monthly=0)
    assert datetime(2026, 10, 10, 3, 0) in keep
    assert len(keep) == 4


def test_同一天多份只算一天():
    stamps = [datetime(2026, 10, 11, 3, 0), datetime(2026, 10, 11, 9, 30), datetime(2026, 10, 10, 3, 0)]
    assert job.keep_set(stamps, daily=1, weekly=0, monthly=0) == {datetime(2026, 10, 11, 9, 30)}


def test_到点且今天还没备才备():
    at = time(3, 0)
    today = date(2026, 10, 12)
    assert not job.is_due(datetime(2026, 10, 12, 2, 59), set(), at)
    assert job.is_due(datetime(2026, 10, 12, 3, 0), set(), at)
    # 开机晚了，当天补一份
    assert job.is_due(datetime(2026, 10, 12, 9, 0), {date(2026, 10, 11)}, at)
    assert not job.is_due(datetime(2026, 10, 12, 9, 0), {today}, at)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    data = tmp_path / "data"
    for vol in job.VOLUMES:
        (data / vol).mkdir(parents=True)
    root = tmp_path / "backups"
    root.mkdir()
    monkeypatch.setattr(job, "DATA_ROOT", data)
    return root, data


def test_文件卷备份后恢复成备份时的样子(dirs):
    root, data = dirs
    (data / "files" / "a").mkdir()
    (data / "files" / "a" / "qr.png").write_bytes(b"\x89PNG-1")
    (data / "files" / "立项书.pdf").write_bytes(b"pdf")
    (data / "oplog" / "2026-10-12-7.jsonl").write_text('{"x":1}\n')

    made = job.make_backup("manual", root=root, now=datetime(2026, 10, 12, 10, 0), with_db=False)
    assert made.name == "2026-10-12_1000_manual"
    assert not any(p.name.startswith(job.TMP_PREFIX) for p in root.iterdir())

    # 备份之后有改动、有新增
    (data / "files" / "a" / "qr.png").write_bytes(b"changed")
    (data / "files" / "new.png").write_bytes(b"new")
    (data / "oplog" / "2026-10-12-7.jsonl").write_text('{"x":1}\n{"x":2}\n')

    job.restore(made.name, "files", root=root)
    assert (data / "files" / "a" / "qr.png").read_bytes() == b"\x89PNG-1"
    assert not (data / "files" / "new.png").exists()
    assert (data / "files" / "立项书.pdf").read_bytes() == b"pdf"
    assert (data / "oplog" / "2026-10-12-7.jsonl").read_text() == '{"x":1}\n'


def test_备份时按策略删掉旧的(dirs):
    root, _ = dirs
    for t in _days(datetime(2026, 9, 1, 3, 0), 41):
        job.make_backup("daily", root=root, now=t, with_db=False)
    for i in range(12):
        job.make_backup("manual", root=root, now=datetime(2026, 10, 1, 12, i), with_db=False)
    names = [n for n, _, _ in job.list_backups(root)]
    daily = [n for n in names if n.endswith("_daily")]
    manual = [n for n in names if n.endswith("_manual")]
    # 9/1 起每天一份到 10/11：最近 7 天 10/5～10/11，每周 9/20、9/27、10/4、10/11，
    # 每月 9/30、10/11（只有两个月）
    assert daily == [
        "2026-09-20_0300_daily", "2026-09-27_0300_daily", "2026-09-30_0300_daily",
        *[f"2026-10-{d:02d}_0300_daily" for d in range(4, 12)],
    ]
    assert manual == [f"2026-10-01_12{i:02d}_manual" for i in range(2, 12)]


def test_备份中途失败不留半截(dirs, monkeypatch):
    root, data = dirs
    (data / "oplog").rmdir()
    with pytest.raises(RuntimeError):
        job.make_backup("manual", root=root, now=datetime(2026, 10, 12, 10, 0), with_db=False)
    assert list(root.iterdir()) == []


def test_不认识的文件夹不动(dirs):
    root, _ = dirs
    (root / "我的拷贝").mkdir()
    (root / "2026-01-01_0300_daily").mkdir()
    job.prune(root)
    assert (root / "我的拷贝").is_dir()


def test_连接串转成_pg_dump_认的写法(monkeypatch):
    monkeypatch.setenv("YVRUC_DATABASE_URL", "postgresql+psycopg://u:p@db.ruc.edu.cn:5432/yvruc?sslmode=require")
    assert job.libpq_url() == "postgresql://u:p@db.ruc.edu.cn:5432/yvruc?sslmode=require"
