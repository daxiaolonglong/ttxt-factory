"""P5 测试:队列生命周期、失败重试、跨进程文件锁、线程串行、模型客户端契约。

模型侧只做只读探测(/health、/v1/models);chat 契约由进程内桩服务验证,
测试不对 Strata 发起推理,也不占用 GPU。
"""

import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ttxt import db as system_db
from ttxt.model_gate import client, gate, queue


def _tmp_db(tmp_path):
    return system_db.init_system_db(tmp_path / "system.db")


def test_queue_lifecycle(tmp_path):
    db_path = _tmp_db(tmp_path)
    job_id = queue.enqueue("chat", {"prompt": "hi"}, category="demo", db_path=db_path)
    row = queue.claim_next(db_path=db_path)
    assert row is not None
    assert row["id"] == job_id
    assert row["status"] == "running"
    assert queue.claim_next(db_path=db_path) is None

    queue.finish(job_id, {"text": "ok"}, db_path=db_path)
    con = sqlite3.connect(db_path)
    try:
        status, result = con.execute(
            "SELECT status, result_json FROM model_jobs WHERE id=?", (job_id,)
        ).fetchone()
        assert status == "done"
        assert '"text"' in result
    finally:
        con.close()


def test_failure_requeue_then_fail(tmp_path):
    db_path = _tmp_db(tmp_path)
    job_id = queue.enqueue("chat", {"prompt": "x"}, db_path=db_path)
    statuses = []
    for _ in range(3):
        queue.claim_next(db_path=db_path)
        statuses.append(queue.record_failure(job_id, "boom", db_path=db_path))
    assert statuses == ["queued", "queued", "failed"]
    record = queue.get(job_id, db_path=db_path)
    assert record["status"] == "failed"
    assert record["last_error"] == "boom"


def test_file_lock_serializes(tmp_path):
    lock_path = tmp_path / "gate.lock"
    lock = gate.FileLock(lock_path, timeout_s=1.0)
    lock.acquire()
    outcomes = []

    def contender():
        try:
            with gate.FileLock(lock_path, timeout_s=0.3):
                outcomes.append("acquired")
        except TimeoutError:
            outcomes.append("timeout")

    thread = threading.Thread(target=contender)
    thread.start()
    thread.join()
    lock.release()
    assert outcomes == ["timeout"]
    with gate.FileLock(lock_path, timeout_s=1.0):
        pass


def test_serial_processor_threads(tmp_path):
    db_path = _tmp_db(tmp_path)
    lock_path = tmp_path / "gate.lock"
    events = []

    def processor(row):
        started = time.monotonic()
        time.sleep(0.2)
        events.append((started, time.monotonic(), row["id"]))
        return {"text": "ok"}

    def worker(prompt):
        gate.submit_and_run(
            "chat",
            {"prompt": prompt},
            processor=processor,
            db_path=db_path,
            gate_lock=lock_path,
            wait_timeout_s=20,
        )

    threads = [threading.Thread(target=worker, args=(f"job{i}",)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(events) == 3
    intervals = sorted((start, end) for start, end, _ in events)
    for (_start_prev, end_prev), (start_next, _end_next) in zip(intervals, intervals[1:]):
        assert end_prev <= start_next + 1e-6


def test_wait_ready_timeout():
    with pytest.raises(client.ModelError):
        client.wait_ready(base_url="http://127.0.0.1:9", timeout_s=1.0, poll_s=0.2)


def test_strata_readonly_probe():
    """只读探测 Strata:仅 /health 与 /v1/models,绝不触发推理、不占用 GPU。"""
    health = client.health()
    if health is None:
        pytest.skip("Strata 未运行(/health 不可达)")
    assert health.get("service") == "strata"
    model_ids = client.models()
    assert model_ids
    if health.get("loaded"):
        assert health.get("model") in model_ids


@pytest.fixture()
def stub_model_server():
    """进程内桩 OpenAI 兼容服务:验证 chat 客户端契约,替代对真实模型的调用。"""

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, payload: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 命名约定
            if self.path == "/health":
                payload = {"status": "ok", "loaded": True, "service": "strata", "model": "stub-model"}
            else:
                payload = {"object": "list", "data": [{"id": "stub-model"}]}
            self._respond(json.dumps(payload).encode("utf-8"))

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            Handler.request_body = json.loads(self.rfile.read(length).decode("utf-8"))
            Handler.request_path = self.path
            self._respond(
                json.dumps(
                    {
                        "model": "stub-model",
                        "choices": [
                            {"message": {"content": "stub reply", "reasoning_content": ""}, "finish_reason": "stop"}
                        ],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                    }
                ).encode("utf-8")
            )

        def log_message(self, *args):  # 静默访问日志
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    Handler.request_body = None
    Handler.request_path = None
    try:
        yield f"http://{host}:{port}", Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_client_chat_uses_stub_not_strata(stub_model_server):
    base_url, handler = stub_model_server
    client._MODEL_CACHE = []
    try:
        result = client.chat(
            [{"role": "user", "content": "hi"}],
            max_tokens=32,
            temperature=0.0,
            base_url=base_url,
            timeout_s=5.0,
            enable_thinking=True,
        )
        assert result["text"] == "stub reply"
        assert result["model"] == "stub-model"
        assert handler.request_path == "/v1/chat/completions"
        assert handler.request_body["model"] == "stub-model"  # 由桩的 /v1/models 解析
        assert handler.request_body["max_tokens"] == 32
        assert handler.request_body["stream"] is False
        assert handler.request_body["chat_template_kwargs"] == {"enable_thinking": True}
        assert handler.request_body["messages"][0]["content"] == "hi"
    finally:
        client._MODEL_CACHE = []


def test_client_chat_pins_model_id_and_survives_empty_completion(stub_model_server):
    base_url, handler = stub_model_server
    client._MODEL_CACHE = []
    try:
        result = client.chat([{"role": "user", "content": "x"}], base_url=base_url, timeout_s=5.0, model_id="pinned-model")
        assert handler.request_body["model"] == "pinned-model"  # 显式 model_id 不再回退 /v1/models
        assert result["model"] == "stub-model"  # 响应里的 model 原样带出
    finally:
        client._MODEL_CACHE = []

    class EmptyHandler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            payload = json.dumps({"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            return

    empty_server = ThreadingHTTPServer(("127.0.0.1", 0), EmptyHandler)
    thread = threading.Thread(target=empty_server.serve_forever, daemon=True)
    thread.start()
    host, port = empty_server.server_address[:2]
    try:
        with pytest.raises(client.ModelError, match="empty completion"):
            client.chat([{"role": "user", "content": "x"}], base_url=f"http://{host}:{port}", timeout_s=5.0, model_id="pinned-model")
    finally:
        empty_server.shutdown()
        empty_server.server_close()
        thread.join(timeout=5)
