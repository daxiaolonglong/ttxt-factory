"""P7 测试:面板静态页/API/错误处理/类目隔离,六角色文件。"""

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


@pytest.fixture()
def dashboard():
    httpd = server.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    yield f"http://{host}:{port}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


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


def test_categories_isolation(dashboard):
    _, body = _get(dashboard + "/api/categories")
    cats = {c["slug"]: c for c in json.loads(body)}
    assert "technology" in cats
    assert "demo" in cats

    def count_articles(slug: str) -> int:
        db_path = Path("D:/ttxt/01_categories") / slug / "articles.db"
        if not db_path.exists():
            return 0
        con = sqlite3.connect(db_path)
        try:
            return con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        finally:
            con.close()

    # 每个类目的 API 计数必须等于该类目独立数据库的真实计数(物理隔离)
    assert cats["technology"]["articles"] == count_articles("technology")
    assert cats["demo"]["articles"] == count_articles("demo")
    assert cats["demo"]["articles"] >= 20


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


def test_export_project_skills():
    created = agents.export_skills()
    assert len(created) == 6
    for path_str in created:
        path = Path(path_str)
        assert path.exists()
        assert path.parent.parent == Path("D:/ttxt/.dsh/skills")
        head = path.read_text(encoding="utf-8").splitlines()[:4]
        assert head[0] == "---"
        assert head[1].startswith("name: ttxt-")
        assert head[2].startswith("description: ")
