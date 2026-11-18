"""T-17 千万级压力外推的证据件:50 万行隔离库实测清洗/聚类/索引吞吐.

库落在 ``07_tmp/scale_bench/articles.db``(与 01_categories 生产库完全隔离,
类目标签 scalebench 不注册进 category_registry,不参与任何生产链路)。
跑完由 ``--purge`` 删除,绝不留临时库膨胀磁盘。

实测口径(不许外推时偷换):
- seed 50 万行真实长度中文语料(600~900 字),含 0.5% 完全重复 + 1% 近重复(改词级),
  其余高多样性,模拟真实头条语料的重复结构;
- 第 1 轮 ``full=True``:冷台账全量(清洗+聚类+FTS 全量),回执 stage_seconds;
- 第 2 轮增量空跑:指纹台账全复用,回执增量成本;
- 每行单位成本 × 千万 = 外推值,只作规划参考,不当实测宣称。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ttxt.crawler import pipeline, store  # noqa: E402

BENCH_DIR = ROOT / "07_tmp" / "scale_bench"
BENCH_DB = BENCH_DIR / "articles.db"
BENCH_CAT = "scalebench"

TITLES = [
    "城市就业新规落地首月观察", "教育减负后的课外市场", "房价与租赁市场季度盘点",
    "制造业招工难调查", "县域消费回暖的真实成色", "养老金调整方案解读",
    "外卖骑手社保试点进展", "新能源汽车下乡调查", "社区养老食堂运营样本",
    "青年返乡创业记账", "城中村改造资金从哪来", "职业教育招生热度调查",
]
TOKENS = [
    "调研", "样本", "同比", "环比", "口径", "基数", "覆盖", "渗透", "产能", "订单",
    "库存", "回款", "毛利", "补贴", "试点", "备案", "批复", "清单", "台账", "回访",
    "城区", "县域", "乡镇", "园区", "车间", "门店", "站点", "班组", "农户", "商户",
    "上月", "上季度", "去年同期", "环比上月", "全年", "半年度", "首月", "第三周",
    "涨了", "回落", "持平", "翻倍", "收窄", "扩大", "企稳", "承压", "回暖", "透支",
]
JUNK = ["据", "该", "相关", "受访", "多位", "多位受访", "公开", "官方", "内部", "业内"]


def make_body(rng: random.Random, length: int) -> str:
    out = []
    while sum(len(w) + 1 for w in out) < length:
        out.append(rng.choice(TOKENS))
        if rng.random() < 0.15:
            out.append(rng.choice(JUNK))
    return "".join(out)


def seed(rows: int) -> float:
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    path = store.migrate_articles_db(BENCH_CAT, db_path=BENCH_DB)
    from ttxt.db import connect as _connect
    con = _connect(path)
    started = time.perf_counter()
    rng = random.Random(20261118)
    try:
        now_iso = "2026-11-18T00:00:00+08:00"
        batch = []

        def row_tuple(i: int, title: str, body: str, day: str) -> tuple:
            url = f"https://example.com/bench/{i}"
            return (BENCH_CAT, "scalebench", title, body, day, url, url, now_iso, hashlib.sha1(url.encode()).hexdigest())

        for i in range(rows):
            title = rng.choice(TITLES) + f" #{i}"
            body = make_body(rng, rng.randint(600, 900))
            batch.append(row_tuple(i, title, body, "2026-11-%02d" % rng.randint(1, 17)))
            if len(batch) >= 20000:
                con.executemany(
                    "INSERT INTO articles(category, source, title, content, published_at,"
                    " url, url_normalized, fetched_at, content_hash) VALUES(?,?,?,?,?,?,?,?,?)",
                    batch,
                )
                batch.clear()
                con.commit()
        if batch:
            con.executemany(
                "INSERT INTO articles(category, source, title, content, published_at,"
                " url, url_normalized, fetched_at, content_hash) VALUES(?,?,?,?,?,?,?,?,?)",
                batch,
            )
            con.commit()
        # 近重复注入:复制 1% 的正文,替换少量词;0.5% 完全重复(换 URL)。
        src = con.execute("SELECT id, title, content, published_at FROM articles ORDER BY RANDOM() LIMIT ?", (int(rows * 0.01),)).fetchall()
        dup_exact = con.execute("SELECT id, title, content, published_at FROM articles ORDER BY RANDOM() LIMIT ?", (int(rows * 0.005),)).fetchall()
        inj = []
        for row in src:
            words = row["content"]
            cut = rng.randint(0, max(0, len(words) - 40))
            mutated = words[:cut] + "调研发回款台账" + words[cut + 6:]
            inj.append(row_tuple(1_000_000_000 + int(row["id"]), row["title"] + " (重发)", mutated, row["published_at"]))
        for row in dup_exact:
            inj.append(row_tuple(2_000_000_000 + int(row["id"]), row["title"], row["content"], row["published_at"]))
        con.executemany(
            "INSERT INTO articles(category, source, title, content, published_at,"
            " url, url_normalized, fetched_at, content_hash) VALUES(?,?,?,?,?,?,?,?,?)",
            inj,
        )
        con.commit()
        total = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    finally:
        con.close()
    elapsed = round(time.perf_counter() - started, 1)
    print(json.dumps({"phase": "seed", "target": rows, "inserted_total": total, "elapsed_s": elapsed}, ensure_ascii=False))
    sys.stdout.flush()
    return elapsed


def db_sizes() -> dict:
    sizes = {}
    for p in sorted(BENCH_DIR.glob("articles.db*")):
        sizes[p.name] = round(p.stat().st_size / 1e6, 1)
    return sizes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=500_000)
    ap.add_argument("--skip-seed", action="store_true")
    ap.add_argument("--purge", action="store_true")
    args = ap.parse_args()
    if args.purge:
        for p in BENCH_DIR.glob("articles.db*"):
            p.unlink()
        print(json.dumps({"purged": [p.name for p in BENCH_DIR.glob('*')]}, ensure_ascii=False))
        return 0
    if not args.skip_seed:
        seed(args.rows)
    t0 = time.perf_counter()
    full = pipeline.run_pipeline(BENCH_CAT, db_path=BENCH_DB, full=True)
    print(json.dumps({"phase": "full_pipeline", "receipt": full, "db_sizes_mb": db_sizes()}, ensure_ascii=False))
    sys.stdout.flush()
    t1 = time.perf_counter()
    incr = pipeline.run_pipeline(BENCH_CAT, db_path=BENCH_DB)
    print(json.dumps({"phase": "incremental_pipeline", "receipt": incr, "db_sizes_mb": db_sizes()}, ensure_ascii=False))
    print(f"ELAPSED_S full={round(t1 - t0, 1)} incr={round(time.perf_counter() - t1, 1)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
