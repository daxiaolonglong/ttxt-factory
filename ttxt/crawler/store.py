"""类目 articles.db 写入与采集任务记录(P2 最小 schema)。"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from ttxt import config as cfg
from ttxt.crawler import cleaner
from ttxt.crawler.clean import content_hash, normalize_url
from ttxt.crawler.extract import ExtractedItem
from ttxt.db import connect, system_db_path
from ttxt.logging import get_logger

LOG = get_logger("crawler.store")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
PROCESSOR_VERSION = "p2-v1"
SCHEMA_VERSION = "p12-v1"
CROSS_READ_CHUNK = 400
# 跨库只读查询允许使用的列(白名单,防止把外部字符串拼进 SQL)
CROSS_ID_COLUMNS = {"url_normalized", "content_hash"}

# C-1:正文完整性取值词表 + 与 Python 侧一致的长度口径(SQL 版,供体检与整改批量用)
COMPLETENESS_VALUES = cleaner.COMPLETENESS_VALUES
COMPLETENESS_THRESHOLD = cleaner.MIN_FULL_CHARS
USABLE_LEN_SQL = (
    "LENGTH(TRIM(REPLACE(REPLACE(REPLACE(COALESCE(content, ''), char(9), ''),"
    " char(10), ''), char(13), '')))"
)


class CompletenessConflict(RuntimeError):
    """整改正文完整性标签时,行的当前值与计划记录不符(有人已经改过)。

    抛出即整笔回滚:绝不做部分提交,也绝不覆盖别人的改动。
    """


ARTICLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  source TEXT NOT NULL,
  source_item_id TEXT,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  excerpt TEXT,
  author TEXT,
  published_at TEXT,
  url TEXT NOT NULL,
  url_normalized TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  completeness TEXT NOT NULL DEFAULT 'full',
  credibility TEXT NOT NULL DEFAULT 'unverified',
  processor_version TEXT NOT NULL DEFAULT 'p2-v1'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_url_norm ON articles(url_normalized);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE TABLE IF NOT EXISTS articles_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

CLUSTERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS article_clusters (
  cluster_id TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  representative_id INTEGER NOT NULL,
  member_count INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

# P3 schema v2 新增列(ALTER TABLE 幂等添加)
P3_COLUMNS: list[tuple[str, str]] = [
    ("cleaned_content", "TEXT"),
    ("text_length", "INTEGER"),
    ("simhash", "TEXT"),
    ("cluster_id", "TEXT"),
    ("cleaned_at", "TEXT"),
    ("clean_version", "TEXT"),
]

V3_SCHEMA = """
CREATE TABLE IF NOT EXISTS keywords (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL,
  category TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'seed',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  UNIQUE(keyword, category)
);
CREATE TABLE IF NOT EXISTS keyword_performance (
  keyword_id INTEGER PRIMARY KEY,
  collected INTEGER NOT NULL DEFAULT 0,
  quality_articles INTEGER NOT NULL DEFAULT 0,
  avg_text_length REAL,
  score REAL,
  last_used_at TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(keyword_id) REFERENCES keywords(id)
);
CREATE TABLE IF NOT EXISTS viral_labels (
  article_id INTEGER PRIMARY KEY,
  label TEXT NOT NULL,
  confidence_tier TEXT NOT NULL,
  score REAL,
  reason TEXT,
  features_json TEXT,
  labeled_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS article_metrics (
  article_id INTEGER PRIMARY KEY,
  reads INTEGER,
  likes INTEGER,
  favorites INTEGER,
  comments INTEGER,
  impressions INTEGER,
  account_baseline TEXT,
  data_source TEXT NOT NULL,
  credibility TEXT NOT NULL DEFAULT 'unverified',
  captured_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  article_id INTEGER NOT NULL,
  captured_at TEXT NOT NULL,
  reads INTEGER,
  likes INTEGER,
  favorites INTEGER,
  comments INTEGER,
  impressions INTEGER,
  data_source TEXT
);
CREATE TABLE IF NOT EXISTS knowledge_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  kind TEXT NOT NULL,
  statement TEXT NOT NULL,
  evidence_json TEXT,
  support_count INTEGER NOT NULL DEFAULT 0,
  confidence TEXT NOT NULL DEFAULT 'exploratory',
  updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(article_id UNINDEXED, title, body);
"""

V4_SCHEMA = """
CREATE TABLE IF NOT EXISTS writing_history (
  draft_id TEXT NOT NULL,
  category TEXT NOT NULL,
  topic TEXT NOT NULL,
  version INTEGER NOT NULL,
  content TEXT NOT NULL,
  model_id TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (draft_id, version)
);
CREATE TABLE IF NOT EXISTS evaluation_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  evaluator TEXT NOT NULL,
  criteria_json TEXT,
  score REAL,
  issues_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id TEXT NOT NULL,
  claim TEXT NOT NULL,
  article_id INTEGER,
  quote TEXT,
  overlap REAL,
  credibility TEXT,
  source_url TEXT,
  checks_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prediction_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT NOT NULL,
  subject TEXT NOT NULL,
  predicted_score REAL,
  basis TEXT,
  predicted_at TEXT NOT NULL,
  outcome_score REAL,
  outcome_at TEXT,
  notes TEXT
);
"""


def migrate_articles_db(category: str, db_path: Path | None = None) -> Path:
    """P2 → P3 → P4 → P6:清洗/聚类列、分析表、FTS5 索引与写作表,幂等。"""
    target = init_articles_db(category, db_path=db_path)
    # 实测教训(P15/C-4):测试里把 init_articles_db 打桩成返回 None,迁移就拿着 None
    # 去 connect,sqlite 把 str(None) 当相对路径,于是仓库根出现一个名为 ``None`` 的库文件。
    # 路径没解析出来就必须当场失败,绝不允许"就地造出一个谁都没要的库"。
    if not isinstance(target, (str, Path)):
        raise TypeError(
            f"init_articles_db({category!r}) 没有返回数据库路径(得到 {target!r}),拒绝继续迁移"
        )
    con = connect(target)
    try:
        existing = {row[1] for row in con.execute("PRAGMA table_info(articles)")}
        for name, ddl in P3_COLUMNS:
            if name not in existing:
                con.execute(f"ALTER TABLE articles ADD COLUMN {name} {ddl}")
        con.executescript(CLUSTERS_SCHEMA)
        con.executescript(V3_SCHEMA)
        con.executescript(V4_SCHEMA)
        writing_columns = {row[1] for row in con.execute("PRAGMA table_info(writing_history)")}
        if "status" not in writing_columns:
            con.execute("ALTER TABLE writing_history ADD COLUMN status TEXT NOT NULL DEFAULT 'draft'")
        # P12 批次4:证据链升级 —— 来源URL + 逐事实核验结果(幂等)
        evidence_columns = {row[1] for row in con.execute("PRAGMA table_info(evidence_links)")}
        for name, ddl in (("source_url", "TEXT"), ("checks_json", "TEXT")):
            if name not in evidence_columns:
                con.execute(f"ALTER TABLE evidence_links ADD COLUMN {name} {ddl}")
        con.execute(
            "INSERT OR REPLACE INTO articles_meta(key, value) VALUES('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        con.commit()
    finally:
        con.close()
    return target


def category_dir(category: str) -> Path:
    if not SLUG_RE.match(category or ""):
        raise ValueError(f"invalid category slug: {category!r}")
    return cfg.CATEGORIES_DIR / category


def articles_db_path(category: str) -> Path:
    return category_dir(category) / "articles.db"


# ------------------------------------------------------------------ C-3 命名空间与跨库只读判定


def registered_namespaces() -> set[str]:
    """注册表(``00_control/category_registry.json``)里的正式类目 slug。

    注册表读不出来时返回空集:任何库都不具备拦截写入的所有权。这个方向是故意的——
    体检/卫生问题不得让采集停摆(``doctor`` 会把注册表本身读失败报成 FAIL)。
    """
    try:
        registry = cfg.load_registry()
    except (OSError, ValueError):
        return set()
    slugs: set[str] = set()
    for item in registry.get("categories", []):
        slug = str(item.get("slug", "")) if isinstance(item, dict) else ""
        if SLUG_RE.match(slug):
            slugs.add(slug)
    return slugs


def namespaces_on_disk() -> list[str]:
    """``01_categories`` 下已经存在 ``articles.db`` 的命名空间目录名(排序)。

    注册表之外的目录(如 ``demo``)也算命名空间,只是它没有数据所有权 —— 见
    ``_namespace_blockers``。空目录(没有库)不参与。
    """
    root = Path(cfg.CATEGORIES_DIR)
    if not root.exists():
        return []
    names: list[str] = []
    for entry in root.iterdir():
        if entry.is_dir() and SLUG_RE.match(entry.name) and (entry / "articles.db").is_file():
            names.append(entry.name)
    return sorted(names)


def namespace_of_db(target: Path) -> str | None:
    """写入目标是否位于类目命名空间根下(``01_categories/<slug>/articles.db``)。

    C-3 判定:命名空间互斥只在类目命名空间库上生效。显式 ``db_path`` 指向命名空间
    之外的一次性库(单测夹具、临时分析库)没有命名空间身份,也不参与互斥——
    它们不在 ``01_categories`` 下,不会污染正式语料。生产写入路径(``runner`` 不传
    ``db_path``)永远落在命名空间内,因此永远受管。
    """
    root = Path(cfg.CATEGORIES_DIR)
    try:
        resolved_root = root.resolve()
        rel = Path(target).resolve().relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    parts = rel.parts
    if len(parts) == 2 and parts[1] == "articles.db" and SLUG_RE.match(parts[0]):
        return parts[0]
    return None


def read_only_connect(path: Path) -> sqlite3.Connection:
    """跨库只读连接:不改 journal_mode、不建表、不跑迁移,只允许读。

    不用 ``file:...?mode=ro`` URI:WAL 库在 ``-wal/-shm`` 边车不存在时只读打开会
    直接失败(SQLite 必须能创建边车),会把正常库误报成不可用。只读约束由
    ``PRAGMA query_only=ON`` 保证;副作用仅为 WAL 边车的创建与清理,与项目里其它
    只读路径(``count_articles`` 等)一致。
    """
    con = sqlite3.connect(str(path), timeout=30.0)
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def _column_hits(con: sqlite3.Connection, column: str, values: set[str]) -> list[str]:
    """在单个库内按列查存在性(IN 分片;绝不跨库 JOIN)。"""
    if column not in CROSS_ID_COLUMNS:  # 防御:列名只允许白名单
        raise ValueError(f"unsupported identity column: {column!r}")
    if not values:
        return []
    hits: list[str] = []
    ordered = sorted(values)
    for start in range(0, len(ordered), CROSS_READ_CHUNK):
        chunk = ordered[start : start + CROSS_READ_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows = con.execute(
            f"SELECT DISTINCT {column} AS value FROM articles WHERE {column} IN ({placeholders})",
            chunk,
        )
        hits.extend(str(row[0]) for row in rows)
    return hits


def cross_namespace_hits(
    urls: set[str], hashes: set[str], exclude_path: Path | None = None
) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
    """只读扫描其它命名空间是否已经持有这些 URL / content_hash。

    返回 ``({命名空间: {"urls": [...], "hashes": [...]}}, 扫描失败的命名空间说明)``。
    这是写入侧的**只读存在性查询**,不是跨库 JOIN,也不把不同类目库合并成检索
    上下文(``08_wiki/db_schema.md`` §3 的类目物理隔离仍然成立)。
    """
    holders: dict[str, dict[str, list[str]]] = {}
    failures: list[str] = []
    if not urls and not hashes:
        return holders, failures
    exclude_resolved = None
    if exclude_path is not None:
        try:
            exclude_resolved = Path(exclude_path).resolve()
        except OSError:
            exclude_resolved = None
    for name in namespaces_on_disk():
        path = articles_db_path(name)
        if exclude_resolved is not None:
            try:
                if path.resolve() == exclude_resolved:
                    continue
            except OSError:
                continue
        try:
            con = read_only_connect(path)
        except sqlite3.Error as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        try:
            url_hits = _column_hits(con, "url_normalized", urls)
            hash_hits = _column_hits(con, "content_hash", hashes)
        except sqlite3.Error as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            url_hits = hash_hits = []
        finally:
            con.close()
        if url_hits or hash_hits:
            holders[name] = {"urls": url_hits, "hashes": hash_hits}
    return holders, failures


def _namespace_blockers(holders: dict[str, dict[str, list[str]]]) -> tuple[set[str], set[str]]:
    """哪些命中真的拦截写入:只有**注册类目**持有才算所有权。

    未注册命名空间(``demo`` 这类测试命名空间)没有数据所有权:它不得拦下正式类目的
    采集,否则测试数据会饿死正式语料(生产实测 ``sspai_rss``/``ruanyifeng_atom`` 在
    P12 由 ``demo=true`` 转正,13 篇早期落在 demo 库,若 demo 有拦截权,这 13 篇
    永远进不了 technology 库)。反过来,注册类目持有的文章不得再复制进 demo 库。
    """
    registered = registered_namespaces()
    blocked_urls: set[str] = set()
    blocked_hashes: set[str] = set()
    for name, hit in holders.items():
        if name not in registered:
            continue
        blocked_urls.update(hit["urls"])
        blocked_hashes.update(hit["hashes"])
    return blocked_urls, blocked_hashes


def init_articles_db(category: str, db_path: Path | None = None) -> Path:
    target = Path(db_path) if db_path is not None else articles_db_path(category)
    target.parent.mkdir(parents=True, exist_ok=True)
    con = connect(target)
    try:
        con.executescript(ARTICLES_SCHEMA)
        con.execute(
            "INSERT OR REPLACE INTO articles_meta(key, value) VALUES('schema_version', ?)",
            (PROCESSOR_VERSION,),
        )
        con.commit()
    finally:
        con.close()
    return target


def _same_title(left: str | None, right: str | None) -> bool:
    """标题是否指向同一篇文章(空白折叠 + 大小写无关;不做任何"猜相似")。"""
    return " ".join(str(left or "").split()).casefold() == " ".join(str(right or "").split()).casefold()


def insert_items(
    category: str,
    source_id: str,
    items: list[ExtractedItem],
    db_path: Path | None = None,
    page_url: str | None = None,
    cross_report: dict[str, object] | None = None,
    page_urls: list[str] | None = None,
) -> dict[str, int]:
    """一批条目写入同一个类目库;URL 文章身份 + content_hash 双重去重,整批一个写事务。

    C-2(2026-11-15)判定与理由:

    - 唯一约束只有 ``idx_articles_content_hash``(:37),正文一改就被当成新文章;
      ``idx_articles_url_norm``(:38)是普通索引,不阻止同 URL 重复。所以 URL 身份
      必须在插入前显式检查,不能指望索引。
    - 检查与插入必须处于同一个写事务。``connect()``(``ttxt/db.py:94``)是 WAL +
      ``busy_timeout=30000``,Python 默认隐式事务是 DEFERRED:读不加写锁,
      “查 URL → 插入”之间存在 TOCTOU 窗口,两个并发任务会双双插入。这里用
      ``BEGIN IMMEDIATE`` 先取写锁(与 ``model_gate/queue.py:33`` 同一做法),
      并发者要么等锁,要么在本批提交后看到该行,不会重复插入。
    - 类目库即类目命名空间:URL 身份按整库判定,与行上的 ``category`` 取值无关。
    - ``page_url`` / ``page_urls`` 是来源页/列表页/订阅地址(html 分页每一页都要传进来,
       RSS 是订阅地址)。它们不是文章身份:列表页抽取器在 C-7b 之前把来源页地址赋给
       每一条,RSS 条目缺 link 时也回退到它。这种 URL 按文章身份去重会把列表页正文
       误判为重复(实测:demo 库 ``quotes_toscrape`` 30 行共用
       ``https://quotes.toscrape.com/``),因此这类条目只按 content_hash 去重。
       调用方(只有 ``runner``)必须传入实际抓过的每一页地址。
    - URL 规范化沿用 ``clean.normalize_url``(只去跟踪参数),不额外剥离可能
      区分文章的查询参数。
    - 整批一个事务:任一条异常 → 整批回滚,不留半批数据。

    C-3(2026-11-15)追加:类目命名空间之间互斥(一篇正式文章只允许属于一个正式类目)。

    - 实测(``07_tmp/c3_20261115/cross_pre.json``):磁盘上有 6 个命名空间库,注册表只有
      5 个正式类目,``demo`` 未注册;逐对 url/hash 交集只有 ``demo`` ↔ ``technology``
      一组(13 URL / 13 hash,``sspai_rss`` 10 + ``ruanyifeng_atom`` 3),根因是这两个源
      P12 由 ``demo=true`` 转正(``02_crawler/sources.json`` 注释),而写入侧从来没有
      任何跨库检测,体检也没有任何跨库对账,所以复制是静默发生的。
    - 拦截权只属于**注册类目**:未注册命名空间(``demo``)不得卡正式类目的采集
      (否则测试语料会饿死正式语料);注册类目持有的文章不得再写进 ``demo``。
    - 跨库判定是只读存在性查询(``query_only``,不改对方库、不建库、不跨库 JOIN),
      且只在写入目标确实位于 ``01_categories/<slug>/articles.db`` 时执行 ——
      见 ``namespace_of_db``。
    - 计数仍然诚实:跨库少掉的条目计入 ``duplicates``,绝不静默丢弃。归属写进
      ``cross_report``(``{"blocked": n, "holders": {命名空间: {urls, hashes}}}``),
      由 ``runner`` 放进任务 ``meta_json`` 并写日志,运维能看到“为什么少了一条、
      那条属于谁”。
    - 已知残留风险(诚实记录):跨库检查与目标库写事务不是同一个原子域,
      两个类目同时采同一篇文章仍可能双双落库;兜底是 ``doctor`` 的
      ``db.cross_overlap`` 对账(``ttxt doctor`` 会 FAIL),而不是假装跨文件原子。

    C-7b(2026-11-15)追加:**"同一个 URL 位上装着两篇不同文章"不得静默**。

    - 实测根因:老实现把任何 URL 冲突一律 ``duplicates += 1`` 就继续,既不区分
      "同一篇被重复抓"与"同一 URL 上是两篇不同文章",也不写日志;``demo`` 库那 30 条
      互不相同的文章共用来源页地址,靠 C-7 判重普查才被发现(3 天才看见)。
    - 现在两种塌陷都计数 + ``LOG.warn``:
      (a) **文章级 URL** 上已经存着别的正文(``content_hash`` 不同)→ 计数
          ``url_identity_conflict``,仍然不插入(一个文章身份只允许一行);
       (a2) **P20 先判例外**:同一 URL + 同一标题 + 正文更长 ⇒ 这是同一篇文章拿到
       更完整的正文,按 C-7 保留判据(正文最长者胜)就地升级这一行(保留 id、URL、
       fetched_at),计数 ``bodies_upgraded``,不算塌陷也不新增行。实测 job 64→66:
       第一轮 RSS 摘要 98 字,第二轮同一 URL 文章页 900+ 字,旧规则会把语料质量永久
       锁死在摘要级;标题不同才判塌陷 —— 悄悄改标题才是 demo 30 条那类事故。
      (b) **非身份 URL**(来源页/列表页)上已经存着别的正文 → 照样插入(删了就毁掉
          真实语料,规则 33),同样计数 ``url_identity_conflict``,让塌陷当场可见。
    - 抽取器标了 ``url_is_page_fallback`` 的条目单独计数 ``url_no_item_link``:
      这是"列表页没给逐条链接"的直接信号,整改方向是配 ``link_selector`` 或换列表页,
      而不是继续把来源页地址当文章地址。
    - 没有命中时**不加键**(``test_store_dedupe`` 断言两键字典,``meta_json`` 结构
      也不为特例特判),与 C-3 ``cross_report`` 同一做法。
    """
    # 规范化放在开库之前:坏 URL 直接失败,不会先创建一个空类目库
    normalized = [normalize_url(item.url) for item in items]
    hashes = [content_hash(item.title, item.content) for item in items]
    non_identity_urls: set[str] = set()
    if page_url:
        non_identity_urls.add(normalize_url(page_url))
    for extra in page_urls or []:
        if extra:
            non_identity_urls.add(normalize_url(extra))
    target = Path(db_path) if db_path is not None else init_articles_db(category)
    # 跨库只读扫描:开写事务之前做完,避免在持锁期间读别人的库
    blocked_urls: set[str] = set()
    blocked_hashes: set[str] = set()
    holders: dict[str, dict[str, list[str]]] = {}
    scan_failures: list[str] = []
    if namespace_of_db(target) is not None:
        article_urls = {value for value in normalized if value not in non_identity_urls}
        holders, scan_failures = cross_namespace_hits(article_urls, set(hashes), exclude_path=target)
        blocked_urls, blocked_hashes = _namespace_blockers(holders)
    if cross_report is not None:
        cross_report["blocked"] = 0
        cross_report["holders"] = {
            name: {"urls": sorted(hit["urls"]), "hashes": sorted(hit["hashes"])}
            for name, hit in holders.items()
        }
        if scan_failures:
            cross_report["scan_errors"] = list(scan_failures)
    for failure in scan_failures:
        # 卫生检查不能把采集打停:发现失败要喊出来,由 doctor 的跨库对账兜底
        LOG.warn("insert.cross_db_scan_failed", category=category, detail=failure)
    con = connect(target)
    inserted = duplicates = cross_blocked = identity_conflicts = upgrades = 0
    # C-7b:抽取器明确标了"这一条没拿到自己的链接"的条目单独计数,不得静默
    no_item_link = sum(1 for item in items if getattr(item, "url_is_page_fallback", False))
    fetched_at = cfg.now_iso()
    batch_urls: set[str] = set()  # 本事务内已占用的文章级 URL(批内同 URL 也算重复)
    batch_hashes: dict[str, list[str]] = {}  # 本事务内每个 URL 已写入的正文哈希
    try:
        con.execute("BEGIN IMMEDIATE")
        for item, url_normalized, hash_value in zip(items, normalized, hashes):
            if url_normalized in blocked_urls or hash_value in blocked_hashes:
                # 别的注册类目已经持有这篇文章:本类目不得再存一行
                cross_blocked += 1
                duplicates += 1
                continue
            same_content_elsewhere = hash_value in (batch_hashes.get(url_normalized) or []) or (
                con.execute(
                    "SELECT 1 FROM articles WHERE url_normalized = ? AND content_hash = ? LIMIT 1",
                    (url_normalized, hash_value),
                ).fetchone()
                is not None
            )
            if url_normalized not in non_identity_urls:
                clash = url_normalized in batch_urls
                if not clash:
                    clash = (
                        con.execute(
                            "SELECT 1 FROM articles WHERE url_normalized = ? LIMIT 1",
                            (url_normalized,),
                        ).fetchone()
                        is not None
                    )
                if clash:
                    duplicates += 1
                    if not same_content_elsewhere:
                        # P20(实测 2026-11-15 job 64→66):同一 URL、同一标题,这一轮
                        # 抓到了更长的正文(第一轮 RSS 摘要 98 字 → 第二轮文章页 900+ 字)。
                        # 这是**同一篇文章的正文升级**,不是身份塌陷:按 C-7 的保留判据
                        # (正文最长者胜出)就地升级这一行,不新增行、不删行、不改 id/URL。
                        # 标题不同则仍是塌陷:绝不悄悄改写别人的标题(那才是 demo 30 条事故)。
                        existing = (
                            None
                            if url_normalized in batch_urls
                            else con.execute(
                                "SELECT id, title, content FROM articles WHERE url_normalized = ? LIMIT 1",
                                (url_normalized,),
                            ).fetchone()
                        )
                        if (
                            existing is not None
                            and _same_title(existing["title"], item.title)
                            and len(str(item.content or "")) > len(str(existing["content"] or ""))
                        ):
                            con.execute(
                                "UPDATE articles SET content = ?, excerpt = ?, content_hash = ?,"
                                " completeness = ?, processor_version = ? WHERE id = ?",
                                (
                                    item.content,
                                    item.excerpt,
                                    hash_value,
                                    cleaner.completeness_of(item.content),
                                    PROCESSOR_VERSION,
                                    existing["id"],
                                ),
                            )
                            upgrades += 1
                            LOG.info(
                                "insert.body_upgraded",
                                category=category,
                                source=source_id,
                                url=url_normalized,
                                article_id=existing["id"],
                                old_chars=len(str(existing["content"] or "")),
                                new_chars=len(str(item.content or "")),
                                detail="同一 URL 同一标题,正文由摘要级升级为更完整正文(保留原行与原 id,不新增行)",
                            )
                            continue
                        # 同一个文章身份位上存的是**另一篇**文章:身份塌陷,必须喊出来
                        identity_conflicts += 1
                        LOG.warn(
                            "insert.url_identity_conflict",
                            category=category,
                            source=source_id,
                            url=url_normalized,
                            title=item.title[:80],
                            detail="同一 URL 上已存着正文不同的文章,本条未插入(要修的是抽取器,不是删数据)",
                        )
                    continue
            elif (
                url_normalized in batch_urls
                or con.execute(
                    "SELECT 1 FROM articles WHERE url_normalized = ? LIMIT 1", (url_normalized,)
                ).fetchone()
                is not None
            ) and not same_content_elsewhere:
                # 列表页/订阅地址这种**非身份 URL** 下已经存着别的正文:正文照旧保留
                # (删了就毁掉真实语料,规则 33),但塌陷必须当场计数 + 记日志
                identity_conflicts += 1
                LOG.warn(
                    "insert.page_url_collapse",
                    category=category,
                    source=source_id,
                    url=url_normalized,
                    title=item.title[:80],
                    detail="来源页/列表页地址被当成文章地址,该 URL 上已存着另一篇正文(整改:配 link_selector 或换列表页)",
                )
            cursor = con.execute(
                """
                INSERT OR IGNORE INTO articles(
                  category, source, source_item_id, title, content, excerpt, author,
                  published_at, url, url_normalized, fetched_at, content_hash,
                  completeness, credibility, processor_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    category,
                    source_id,
                    item.source_item_id,
                    item.title,
                    item.content,
                    item.excerpt,
                    item.author,
                    item.published_at,
                    item.url,
                    url_normalized,
                    fetched_at,
                    hash_value,
                    # C-1:如实标注正文完整度(三档见 cleaner.completeness_of);
                    # 不能只判断“有没有正文”——RSS 摘要、列表页正文都会被谎报成全文
                    cleaner.completeness_of(item.content),
                    "unverified",
                    PROCESSOR_VERSION,
                ),
            )
            if cursor.rowcount:
                inserted += 1
                batch_urls.add(url_normalized)
                batch_hashes.setdefault(url_normalized, []).append(hash_value)
            else:
                duplicates += 1
        con.commit()
    except BaseException:
        # 半批数据不得留下:整批回滚后原异常继续上抛,由 runner 记 failed 任务
        try:
            con.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        con.close()
    if cross_report is not None:
        cross_report["blocked"] = cross_blocked
    counts: dict[str, int] = {"inserted": inserted, "duplicates": duplicates}
    # C-7b:只有真的发生才加键,保持两键字典在正常情况下不变(dashboard/meta_json 不用特判)
    if identity_conflicts:
        counts["url_identity_conflict"] = identity_conflicts
        LOG.warn(
            "insert.url_identity_summary",
            category=category,
            source=source_id,
            conflicts=identity_conflicts,
            detail="同一 URL 位上出现了正文互不相同的文章;逐条原因见 insert.url_identity_conflict / insert.page_url_collapse",
        )
    if upgrades:
        # P20:正文升级只有真的发生才加键(与 cross_report/C-7b 同一做法)
        counts["bodies_upgraded"] = upgrades
        LOG.info(
            "insert.body_upgrade_summary",
            category=category,
            source=source_id,
            upgraded=upgrades,
            detail="同一 URL 同一标题的旧行被升级为更完整正文;逐条见 insert.body_upgraded",
        )
    if no_item_link:
        counts["url_no_item_link"] = no_item_link
        LOG.warn(
            "insert.no_item_link",
            category=category,
            source=source_id,
            count=no_item_link,
            detail="这些条目没拿到自己的链接,URL 退回来源页/订阅地址;整改:配 link_selector 或换列表页",
        )
    return counts


# ------------------------------------------------------- C-1 历史标签整改(dry-run / 写入 / 回滚)


def _require_existing_db(category: str, target: Path) -> None:
    """整改只针对已经存在的命名空间库:不得顺手建库(与 C-3 的“不创建”纪律一致)。"""
    if not Path(target).is_file():
        raise FileNotFoundError(f"命名空间库不存在,整改不会建库: {target}")


def distribution_text(counts: dict[str, int]) -> str:
    """把分布渲染成固定顺序的字符串,词表内的 0 值也要显式写出来(便于对比与体检阅读)。"""
    parts = [f"{value}={counts.get(value, 0)}" for value in COMPLETENESS_VALUES]
    extras = sorted(value for value in counts if value not in COMPLETENESS_VALUES)
    parts.extend(f"{value}={counts[value]}" for value in extras)
    return " ".join(parts)


def completeness_plan(category: str, db_path: Path | None = None) -> dict:
    """只读:算出每行**应有**的 ``completeness`` 与当前值不一致的行(逐行计划)。

    返回 ``{category, db, threshold, rows_total, current, planned, changes}``;
    ``changes`` 每项含 ``id / from / to / usable_len``,足以逐行回滚与人工核对。
    """
    target = Path(db_path) if db_path is not None else articles_db_path(category)
    _require_existing_db(category, target)
    con = read_only_connect(target)
    try:
        rows = con.execute(
            f"SELECT id, completeness, {USABLE_LEN_SQL} AS usable_len FROM articles ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    current: dict[str, int] = {}
    planned: dict[str, int] = {}
    changes: list[dict] = []
    for row_id, label, usable_len in rows:
        label = str(label)
        expected = cleaner.completeness_of_length(int(usable_len))
        current[label] = current.get(label, 0) + 1
        planned[expected] = planned.get(expected, 0) + 1
        if label != expected:
            changes.append(
                {"id": int(row_id), "from": label, "to": expected, "usable_len": int(usable_len)}
            )
    return {
        "category": category,
        "db": str(target),
        "threshold": COMPLETENESS_THRESHOLD,
        "values": list(COMPLETENESS_VALUES),
        "rows_total": len(rows),
        "current": current,
        "planned": planned,
        "current_distribution": distribution_text(current),
        "planned_distribution": distribution_text(planned),
        "changes": changes,
    }


def apply_completeness_plan(category: str, changes: list[dict], db_path: Path | None = None) -> int:
    """在单个 ``BEGIN IMMEDIATE`` 事务里按计划改写标签,逐行 CAS(旧值不符 → 整笔回滚)。

    整改只改派生字段 ``completeness``,不删行、不动正文;``from`` 记录旧值,
    因此同一份计划可以喂给 ``restore_completeness`` 精确回滚。
    """
    if not changes:
        return 0
    target = Path(db_path) if db_path is not None else articles_db_path(category)
    _require_existing_db(category, target)
    con = connect(target)
    updated = 0
    try:
        con.execute("BEGIN IMMEDIATE")
        for change in changes:
            to_value = str(change.get("to", ""))
            from_value = str(change.get("from", ""))
            if to_value not in COMPLETENESS_VALUES:
                raise CompletenessConflict(f"计划里的目标取值不在词表内: {change}")
            cursor = con.execute(
                "UPDATE articles SET completeness = ? WHERE id = ? AND completeness = ?",
                (to_value, int(change["id"]), from_value),
            )
            if cursor.rowcount != 1:
                raise CompletenessConflict(
                    f"id={change['id']} 的当前 completeness 与计划记录({from_value!r})不符,"
                    "已有其它写入动过这一行,整笔回滚"
                )
            updated += 1
        con.commit()
    except BaseException:
        try:
            con.rollback()
        except sqlite3.Error:
            pass
        finally:
            con.close()
        raise
    con.close()
    LOG.info(
        "store.completeness_relabelled",
        category=category,
        db=str(target),
        updated=updated,
        threshold=COMPLETENESS_THRESHOLD,
    )
    return updated


def relabel_completeness(category: str, db_path: Path | None = None, apply: bool = False) -> dict:
    """按真实正文长度如实改写 ``completeness``;默认干跑,``apply=True`` 才写。"""
    plan = completeness_plan(category, db_path)
    if not apply:
        return {**plan, "applied": False, "updated": 0}
    updated = apply_completeness_plan(category, plan["changes"], Path(plan["db"]))
    return {**plan, "applied": True, "updated": updated}


def restore_completeness(category: str, changes: list[dict], db_path: Path | None = None) -> dict:
    """按整改报告逐行恢复旧值(回滚);同样带 CAS,冲突即整笔回滚。"""
    target = Path(db_path) if db_path is not None else articles_db_path(category)
    _require_existing_db(category, target)
    if not changes:
        return {"category": category, "db": str(target), "restored": 0}
    con = connect(target)
    restored = 0
    try:
        con.execute("BEGIN IMMEDIATE")
        for change in changes:
            cursor = con.execute(
                "UPDATE articles SET completeness = ? WHERE id = ? AND completeness = ?",
                (str(change.get("from", "")), int(change["id"]), str(change.get("to", ""))),
            )
            if cursor.rowcount != 1:
                raise CompletenessConflict(
                    f"id={change['id']} 的当前 completeness 与整改后的值"
                    f"({change.get('to')!r})不符,回滚整笔撤销"
                )
            restored += 1
        con.commit()
    except BaseException:
        try:
            con.rollback()
        except sqlite3.Error:
            pass
        finally:
            con.close()
        raise
    con.close()
    LOG.warn("store.completeness_restored", category=category, db=str(target), restored=restored)
    return {"category": category, "db": str(target), "restored": restored}


def count_articles(category: str, db_path: Path | None = None) -> int:
    target = Path(db_path) if db_path is not None else articles_db_path(category)
    if not target.exists():
        return 0
    con = connect(target)
    try:
        return int(con.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
    finally:
        con.close()


# ---- 采集任务记录(system.db.crawl_jobs) ----


def create_crawl_job(
    source_id: str,
    category: str,
    db_path: Path | None = None,
    meta: dict | None = None,
) -> int:
    import json

    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        cursor = con.execute(
            "INSERT INTO crawl_jobs(job_type, category, source, status, created_at, meta_json) "
            "VALUES('crawl', ?, ?, 'queued', ?, ?)",
            (category, source_id, cfg.now_iso(), json.dumps(meta or {}, ensure_ascii=False)),
        )
        con.commit()
        return int(cursor.lastrowid)
    finally:
        con.close()


def find_pending_crawl_job(source_id: str, category: str, db_path: Path | None = None) -> int | None:
    """同来源+类目已有 queued/running 任务时返回其 id(用于入队去重)。"""
    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        row = con.execute(
            "SELECT id FROM crawl_jobs WHERE source=? AND category=? AND status IN ('queued','running') "
            "ORDER BY id LIMIT 1",
            (source_id, category),
        ).fetchone()
        return int(row["id"]) if row else None
    finally:
        con.close()


def crawl_job_params(job_id: int, db_path: Path | None = None) -> dict:
    """只读:取任务**入队时**写入的 ``meta_json``(P19 采集计划:关键词/检索地址)。

    为什么单独一个只读入口:``finish_crawl_job``(:889)会用执行回执整体覆盖
    ``meta_json``,所以计划必须在执行前读出来传给 ``runner``,执行后再读就读不到了
    (回执里由 runner 回显 ``plan`` 键,证据仍然完整)。
    """
    import json

    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        row = con.execute("SELECT meta_json FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        con.close()
    if not row:
        return {}
    raw = row["meta_json"]
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception:  # noqa: BLE001 - meta 坏掉不能把采集拖死,按无计划处理并留日志
        LOG.warn("store.crawl_job_params_unparsable", job_id=job_id)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def list_queued_crawl_jobs(
    limit: int = 20,
    category: str | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        where = "status='queued' AND job_type='crawl'"
        params: list = []
        if category:
            where += " AND category=?"
            params.append(category)
        params.append(max(1, int(limit)))
        rows = con.execute(
            f"SELECT id, source, category, attempts, max_attempts, created_at FROM crawl_jobs "
            f"WHERE {where} ORDER BY id LIMIT ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        con.close()


def mark_job_started(job_id: int, db_path: Path | None = None) -> None:
    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        con.execute(
            "UPDATE crawl_jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id=?",
            (cfg.now_iso(), job_id),
        )
        con.commit()
    finally:
        con.close()


def finish_crawl_job(
    job_id: int,
    status: str,
    error: str | None = None,
    meta: dict | None = None,
    db_path: Path | None = None,
) -> None:
    import json

    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        con.execute(
            "UPDATE crawl_jobs SET status=?, last_error=?, finished_at=?, meta_json=? WHERE id=?",
            (status, error, cfg.now_iso(), json.dumps(meta or {}, ensure_ascii=False), job_id),
        )
        con.commit()
    finally:
        con.close()


def abort_crawl_job(job_id: int, error: str, meta: dict | None = None, db_path: Path | None = None) -> None:
    """P12 终态 ``aborted``:配置性失败(来源不存在/停用/类目拒绝)。

    这类失败重试无意义,绝不进入 ``requeue_failed_crawl_jobs``(它只回收 transient
    失败产生的 ``failed``),也不占 attempts 重试额度 —— 杜绝幽灵任务无限重排。
    """
    import json

    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        con.execute(
            "UPDATE crawl_jobs SET status='aborted', last_error=?, finished_at=?, meta_json=? WHERE id=?",
            (error[:2000], cfg.now_iso(), json.dumps(meta or {}, ensure_ascii=False), job_id),
        )
        con.commit()
    finally:
        con.close()


def fail_crawl_job(job_id: int, error: str, meta: dict | None = None, db_path: Path | None = None) -> str:
    """任务失败(网络/抓取等 transient):attempts 未达上限则重新排队,否则 failed。

    注意:只有 transient 失败走这里;配置性失败必须用 ``abort_crawl_job`` 终态化。
    """
    """任务失败:attempts 未达上限则重新排队(queued),否则 failed。"""
    import json

    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        row = con.execute(
            "SELECT attempts, max_attempts FROM crawl_jobs WHERE id=?", (job_id,)
        ).fetchone()
        attempts = int(row["attempts"]) if row else 0
        max_attempts = int(row["max_attempts"]) if row else 3
        status = "queued" if attempts < max_attempts else "failed"
        con.execute(
            "UPDATE crawl_jobs SET status=?, last_error=?, finished_at=?, meta_json=? WHERE id=?",
            (
                status,
                error[:2000],
                cfg.now_iso() if status == "failed" else None,
                json.dumps(meta or {}, ensure_ascii=False),
                job_id,
            ),
        )
        con.commit()
        return status
    finally:
        con.close()


def requeue_failed_crawl_jobs(db_path: Path | None = None) -> int:
    """把 attempts 未达上限的 failed 采集任务重新排队(等待人工/--run 重跑)。"""
    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        cursor = con.execute(
            "UPDATE crawl_jobs SET status='queued', finished_at=NULL "
            "WHERE status='failed' AND attempts < max_attempts"
        )
        con.commit()
        return int(cursor.rowcount)
    finally:
        con.close()


def claim_next_crawl_job(
    owner: str,
    lease_s: float = 900.0,
    category: str | None = None,
    db_path: Path | None = None,
) -> dict | None:
    """P12:原子认领一个 queued 采集任务 —— 进入 running 的唯一通道。

    - 原子性:单条 UPDATE(内含选任务子查询)在一个写事务内完成。多个 Worker
      并发争抢时,WAL 写锁串行化,后来者的 ``id=(SELECT...)`` 命中的行已非
      queued,``fetchone()`` 为空 —— 同一任务不可能被两个执行者拿到。
    - 同源不并发:该 source 已有 running 任务时,同 source 其余任务一律不认领
      (同站并发会破坏限速,并争抢浏览器)。
    - ``attempts`` 在认领时 +1:尝试次数与实际执行严格一致,``max_attempts``
      因此真正封顶失败重试。
    - 只认领 ``job_type='crawl'``:Worker 永不触碰其它类型任务。
    """
    from datetime import datetime, timedelta

    target = Path(db_path) if db_path is not None else system_db_path()
    lease_until = (datetime.now().astimezone() + timedelta(seconds=float(lease_s))).isoformat(
        timespec="seconds"
    )
    where = "status='queued' AND job_type='crawl'"
    params: list = [owner, cfg.now_iso(), lease_until]
    if category:
        where += " AND category=?"
        params.append(category)
    con = connect(target)
    try:
        cur = con.execute(
            f"""
            UPDATE crawl_jobs
               SET status='running', owner=?, started_at=?, lease_expires_at=?, attempts=attempts+1
             WHERE id = (
                SELECT j.id FROM crawl_jobs j
                 WHERE {where}
                   AND NOT EXISTS (
                        SELECT 1 FROM crawl_jobs r
                         WHERE r.source = j.source AND r.status='running'
                   )
                 ORDER BY j.id LIMIT 1
             )
             AND status='queued'
             RETURNING id, source, category, attempts, max_attempts, owner, lease_expires_at
            """,
            params,
        )
        row = cur.fetchone()
        con.commit()
        return dict(row) if row else None
    finally:
        con.close()


def renew_lease(job_id: int, lease_s: float = 900.0, db_path: Path | None = None) -> None:
    """长任务在阶段边界续租(仅当仍为 running)。"""
    from datetime import datetime, timedelta

    target = Path(db_path) if db_path is not None else system_db_path()
    lease_until = (datetime.now().astimezone() + timedelta(seconds=float(lease_s))).isoformat(
        timespec="seconds"
    )
    con = connect(target)
    try:
        con.execute(
            "UPDATE crawl_jobs SET lease_expires_at=? WHERE id=? AND status='running'",
            (lease_until, job_id),
        )
        con.commit()
    finally:
        con.close()


def recover_stale_crawl_jobs(lease_s: float = 1800.0, db_path: Path | None = None) -> int:
    """进程中断恢复:running 且租约过期的任务 → failed(根因:进程中断)。

    优先用认领写入的 ``lease_expires_at``;没有该列值的旧行退回
    ``started_at + lease_s`` 的保守判断。后续由 ``requeue_failed_crawl_jobs``
    在 attempts 未达上限时重新排队。
    """
    from datetime import datetime

    target = Path(db_path) if db_path is not None else system_db_path()
    con = connect(target)
    try:
        rows = con.execute(
            "SELECT id, started_at, lease_expires_at, owner FROM crawl_jobs WHERE status='running'"
        ).fetchall()
        now = datetime.now().astimezone()
        recovered = 0
        for row in rows:
            stale = False
            if row["lease_expires_at"]:
                try:
                    stale = now > datetime.fromisoformat(row["lease_expires_at"])
                except ValueError:
                    stale = False
            elif row["started_at"]:
                try:
                    stale = (now - datetime.fromisoformat(row["started_at"])).total_seconds() > lease_s
                except ValueError:
                    stale = False
            if stale:
                con.execute(
                    "UPDATE crawl_jobs SET status='failed', finished_at=?, "
                    "last_error='recovered: 进程中断(租约过期, owner="
                    + str(row["owner"] or "?")
                    + ")' WHERE id=?",
                    (cfg.now_iso(), row["id"]),
                )
                recovered += 1
        con.commit()
        return recovered
    finally:
        con.close()
