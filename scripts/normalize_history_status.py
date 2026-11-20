# -*- coding: utf-8 -*-
"""R51 P2-4: writing_history.status 大小写幂等归一(只改数据,不改判定逻辑)。

背景:social 库里 REJECTED 与 rejected 并存,technology 库里
AUTO_PASSED/REJECTED/draft/passed/rejected 五种写法混用。写入侧已统一小写
(workflow._review_account/_set_draft_status),本脚本把历史行归一为小写。

纪律:
- 先备份:每个 articles.db 用 sqlite backup API 复制到 07_tmp/r51_status_norm_backup/
  (WAL 内容一起带上,绝不只读打开生产库后直接改);
- 幂等:UPDATE 只命中 status <> lower(status) 的行,重跑第二次改 0 行、结果不变;
- 只动 status 一列,不做任何判定/阈值/门禁逻辑改动;
- 归一前后按 status 计数打印,供对照表引用。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "07_tmp" / "r51_status_norm_backup"


def counts(con: sqlite3.Connection) -> dict[str, int]:
    return {
        row[0]: row[1]
        for row in con.execute("SELECT status, COUNT(*) FROM writing_history GROUP BY status ORDER BY status")
    }


def normalize(db_path: Path, stamp: str) -> bool:
    if not db_path.exists():
        return False
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"{db_path.parent.name}_articles_backup_{stamp}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(backup_path)
    try:
        src.backup(dst)  # 备份先落盘(含 WAL),再动生产库
    finally:
        src.close()
        dst.close()

    con = sqlite3.connect(db_path)
    try:
        before = counts(con)
        cur = con.execute(
            "UPDATE writing_history SET status = lower(status) WHERE status <> lower(status)"
        )
        changed = cur.rowcount
        con.commit()
        after = counts(con)
        # 幂等自证:再跑一次必须 0 行变更
        cur2 = con.execute(
            "UPDATE writing_history SET status = lower(status) WHERE status <> lower(status)"
        )
        second_pass = cur2.rowcount
        con.commit()
        after_recheck = counts(con)
    finally:
        con.close()

    print(f"=== {db_path.parent.name}: backup -> {backup_path}")
    print(f"    before: {before}")
    print(f"    changed_rows(pass1): {changed}")
    print(f"    after: {after}")
    print(f"    changed_rows(pass2,必须为0): {second_pass}")
    print(f"    after==recheck: {after == after_recheck}")
    return True


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    found = 0
    for db in sorted((ROOT / "01_categories").glob("*/articles.db")):
        if normalize(db, stamp):
            found += 1
    if not found:
        print("no articles.db found under 01_categories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
