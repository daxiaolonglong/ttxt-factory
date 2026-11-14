"""P7 测试:面板静态页/API/错误处理/类目隔离,六角色文件。

C-8 隔离:面板必须只在 ``07_tmp`` 下的数据库副本上工作(``isolated_data_root`` 夹具),
生产 ``system.db`` / 类目 ``articles.db`` 在任何断言前后都逐字节不变且没有 WAL/SHM 边车。
"""

import gc
import hashlib
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import pytest

from ttxt import agents
from ttxt.dashboard import server

PRODUCTION_DB_DIRS = (Path("D:/ttxt/00_control"), Path("D:/ttxt/01_categories"))


def _opens_production_db(target: str) -> bool:
    """连接目标是否落在生产库文件上(判定逻辑在本文件自证,不跨测试文件耦合)。"""
    path = Path(target)
    if path.suffix.lower() != ".db":
        return False
    return any(str(path).lower().startswith(str(base).lower()) for base in PRODUCTION_DB_DIRS)


def _production_db_fingerprints() -> dict[str, str]:
    state: dict[str, str] = {}
    for base in PRODUCTION_DB_DIRS:
        for path in sorted(base.rglob("*.db")):
            digest = hashlib.sha256()
            with path.open("rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(block)
            state[path.as_posix()] = f"{path.stat().st_size}:{digest.hexdigest()}"
    return state


def _production_sidecars() -> list[str]:
    return sorted(
        path.as_posix()
        for base in PRODUCTION_DB_DIRS
        for path in base.rglob("*")
        if path.is_file() and path.name.endswith(("-wal", "-shm", "-journal"))
    )


@pytest.fixture()
def dashboard(isolated_data_root: Path):
    """面板夹具:数据库路径全部指向 07_tmp 下的生产副本;日志读重定向后的测试日志目录。"""
    from ttxt import logging as ttxt_logging

    # 播种一条测试日志:C-8 之后面板 /api/logs 读的是隔离目录,必须有数据可查
    ttxt_logging.log_event("info", "p7.dashboard", "p7.log.seed", "面板日志读测试日志目录")
    httpd = server.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)
    # 面板按请求打开副本库;连接对象若还挂在处理器引用环里,SQLite 不会清理 WAL 边车。
    # 强制回收,确保夹具退出后临时目录也没有残留边车(生产目录本来就不该出现任何边车)。
    gc.collect()


def _get(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=15) as response:
        return response.status, response.read().decode("utf-8")


def test_index_and_static(dashboard):
    status, body = _get(dashboard + "/")
    assert status == 200
    assert "头条智能工厂" in body
    status, body = _get(dashboard + "/static/app.js")
    assert status == 200
    assert "fetch(" in body


def test_overview(dashboard):
    status, body = _get(dashboard + "/api/overview")
    data = json.loads(body)
    assert status == 200
    assert {"categories", "disk", "schema_version", "strata"} <= set(data)
    assert data["disk"]["free_gb"] > 0


def test_categories_isolation(dashboard, isolated_data_root: Path):
    _, body = _get(dashboard + "/api/categories")
    cats = {c["slug"]: c for c in json.loads(body)}
    assert "technology" in cats
    assert "demo" in cats

    def count_articles(slug: str) -> int:
        # C-8:面板 API 读的是 07_tmp 下的副本;本测试也在副本上核对真实计数。
        # 副本必须与生产库逐字节一致,否则 API 计数不能代表生产真相。
        production = Path("D:/ttxt/01_categories") / slug / "articles.db"
        copy_path = isolated_data_root / "01_categories" / slug / "articles.db"
        assert copy_path.exists(), f"缺少隔离副本:{copy_path}"
        assert hashlib.sha256(copy_path.read_bytes()).hexdigest() == hashlib.sha256(
            production.read_bytes()
        ).hexdigest(), f"副本与生产库不一致:{slug}"
        con = sqlite3.connect(copy_path)
        try:
            return con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        finally:
            con.close()

    # 每个类目的 API 计数必须等于该类目独立数据库的真实计数(物理隔离)
    assert cats["technology"]["articles"] == count_articles("technology")
    assert cats["demo"]["articles"] == count_articles("demo")
    assert cats["demo"]["articles"] >= 20


def test_dashboard_never_opens_production_databases(dashboard, session_db_opens, isolated_data_root):
    """面板每一个 API 都只能连 07_tmp 下的副本;生产库逐字节不变,且不新增 WAL/SHM 边车。

    边车“谁产生谁负责”:进场时已存在的边车属于测试之外的进程(实测并发的只读探针
    `07_tmp/quality_probe_20261114.py` 用 `mode=ro` URI 打开 6 个类目库),本测试不把它
    算到面板头上,也不靠删除它换取“看起来干净”;这里只保证面板自己不制造新的边车。
    """
    before = _production_db_fingerprints()
    before_sidecars = _production_sidecars()
    if before_sidecars:
        print(f"注意:进场时生产目录已存在边车(非本测试所造),归属取证见 07_tmp/c8_final_db_check.py:{before_sidecars}")
    marker = len(session_db_opens)

    paths = (
        "/api/overview",
        "/api/categories",
        "/api/jobs",
        "/api/logs?limit=10",
        "/api/model",
        "/api/tasks",
        "/api/category/demo/articles?limit=5",
        "/api/category/demo/keywords",
        "/api/category/demo/analysis",
        "/api/category/technology/articles?limit=5",
    )
    for path in paths:
        status, _ = _get(dashboard + path)
        assert status == 200, f"面板 API 未就绪:{path}"

    opened = session_db_opens[marker:]
    assert opened, "记录器没有捕获到面板的任何数据库连接,隔离测试本身失效"
    offenders = sorted({target for target in opened if _opens_production_db(target)})
    assert not offenders, f"面板连接过生产数据库:{offenders}"
    temp_root = Path("D:/ttxt/07_tmp")
    for target in opened:
        if target in (":memory:", ""):
            continue
        assert temp_root in Path(target).parents, f"面板连接了 07_tmp 之外的数据库:{target}"
    assert _production_db_fingerprints() == before, "面板测试改动了生产数据库字节"
    new_sidecars = sorted(set(_production_sidecars()) - set(before_sidecars))
    assert not new_sidecars, f"面板测试在生产目录新增了 WAL/SHM 边车:{new_sidecars}"


def test_category_articles(dashboard):
    _, body = _get(dashboard + "/api/category/demo/articles?limit=5")
    data = json.loads(body)
    assert data["total"] >= 20
    assert 0 < len(data["items"]) <= 5

    _, body = _get(dashboard + f"/api/category/demo/articles?limit=5&q={quote('城市')}")
    data = json.loads(body)
    assert data["total"] >= 1


def test_keywords_and_analysis(dashboard):
    _, body = _get(dashboard + "/api/category/demo/keywords")
    keywords = json.loads(body)
    assert isinstance(keywords, list)
    assert len(keywords) >= 4

    _, body = _get(dashboard + "/api/category/demo/analysis")
    analysis = json.loads(body)
    assert analysis["articles"] >= 20
    assert analysis["viral_tiers"].get("missing_data", 0) >= 20


def test_jobs_logs_model(dashboard):
    _, body = _get(dashboard + "/api/jobs")
    jobs = json.loads(body)
    assert {"crawl", "model"} <= set(jobs)

    _, body = _get(dashboard + "/api/logs?limit=10")
    logs = json.loads(body)
    assert isinstance(logs, list)
    assert len(logs) >= 1

    _, body = _get(dashboard + "/api/model")
    model = json.loads(body)
    assert {"health", "queue"} <= set(model)


def test_error_handling(dashboard):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(dashboard + "/api/nope")
    assert excinfo.value.code == 404

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(dashboard + "/static/..%2F..%2F00_control%2Fconfig.json")
    assert excinfo.value.code in (400, 404)

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(
            urllib.request.Request(dashboard + "/api/overview", method="POST"), timeout=10
        )
    assert excinfo.value.code == 405


def test_agents_roles():
    roles = {role["name"] for role in agents.list_roles()}
    assert {
        "orchestrator",
        "collector",
        "data_engineer",
        "viral_researcher",
        "writer_reviewer",
        "maintainer",
    } <= roles
    content = agents.read_role("orchestrator")
    assert "总调度" in content
    context = agents.role_context("writer_reviewer")
    assert "核心规则" in context
    with pytest.raises(ValueError):
        agents.read_role("../x")


def test_export_project_skills(tmp_path):
    # 导出到临时目录:.dsh/skills 下 6 个 SKILL.md 是 git 跟踪资产,测试不得改写。
    target = tmp_path / "skills"
    created = agents.export_skills(target)
    assert len(created) == 6
    assert agents.SKILLS_ROOT == Path("D:/ttxt/.dsh/skills")
    for path_str in created:
        path = Path(path_str)
        assert path.exists()
        assert path.parent.parent == target
        head = path.read_text(encoding="utf-8").splitlines()[:4]
        assert head[0] == "---"
        assert head[1].startswith("name: ttxt-")
        assert head[2].startswith("description: ")
        # 只读比对:项目内已提交技能必须与角色导出完全一致(同步但未覆写)。
        installed = agents.SKILLS_ROOT / path.parent.name / "SKILL.md"
        assert installed.exists(), f"项目技能缺失:{installed}"
        assert installed.read_bytes() == path.read_bytes(), f"项目技能与角色导出不同步:{installed}"
