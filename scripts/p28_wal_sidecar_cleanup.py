# -*- coding: utf-8 -*-
"""收掉只读巡检在生产目录留下的 SQLite 边车(``-wal``/``-shm``)。

背景:P24 已经查明 ``mode=ro`` 连接打不开 checkpoint,SQLite 会在生产目录留下边车,
下一次可写连接才收掉。本脚本做那"下一次可写连接":逐库
``PRAGMA wal_checkpoint(TRUNCATE)`` + ``PRAGMA quick_check``,干净关闭时 SQLite 自己删掉边车。

不迁数据、不改内容:WAL 里 0 字节 ⇒ 没有未落盘内容;quick_check 必须 ok 才算完成。
边车路径全部来自实际目录列举,不猜。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(r"D:\ttxt\01_categories")


def main() -> int:
    sidecars = sorted(ROOT.glob("*/articles.db-wal")) + sorted(ROOT.glob("*/articles.db-shm"))
    if not sidecars:
        print("没有边车需要处理")
        return 0
    for sidecar in sidecars:
        print(f"边车 {sidecar} 大小={sidecar.stat().st_size}")
    dbs = sorted({p.parent / "articles.db" for p in sidecars})
    failures = 0
    for db in dbs:
        con = sqlite3.connect(db)
        try:
            moved, busy, frames = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            check = con.execute("PRAGMA quick_check").fetchone()[0]
            rows = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        finally:
            con.close()
        left = [str(p) for p in (Path(str(db) + "-wal"), Path(str(db) + "-shm")) if p.exists()]
        print(f"{db}: checkpoint(moved={moved} busy={busy} frames={frames}) quick_check={check}"
              f" articles={rows} 残留边车={left or '无'}")
        if check != "ok" or left:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
