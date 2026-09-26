from pathlib import Path

from services import t2_valet

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── T2 화면 ──

def test_t2_page_renders_form(client):
    resp = client.get("/t2-valet/")
    assert resp.status_code == 200
    assert 'id="name"' in resp.get_data(as_text=True)


def test_t2_without_slash_redirects(client):
    resp = client.get("/t2-valet")
    assert 300 <= resp.status_code < 400
    assert resp.headers["Location"].endswith("/t2-valet/")


def test_t2_page_uses_absolute_static_paths(client):
    html = client.get("/t2-valet/").get_data(as_text=True)
    assert 'src="/static/js/app.js"' in html
    assert 'href="/static/css/tokens.css"' in html


# ── T2 API ──

def test_defaults_returns_interval(client):
    resp = client.get("/t2-valet/api/defaults")
    assert resp.status_code == 200
    assert "interval" in resp.get_json()


def test_logs_returns_logs_and_running(client):
    resp = client.get("/t2-valet/api/logs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["logs"] == []
    assert data["running"] is False


def test_test_call_requires_name_and_phone(client):
    resp = client.post("/t2-valet/api/test", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "이름과 휴대전화는 필수입니다."


def test_start_requires_name_and_phone(client):
    resp = client.post("/t2-valet/api/start", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "이름과 휴대전화는 필수입니다."


def test_start_rejects_interval_below_10(client):
    resp = client.post("/t2-valet/api/start",
                       json={"name": "홍길동", "phone": "01012345678", "interval": 5})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "호출 주기는 최소 10초 이상이어야 합니다."


def test_stop_when_not_running(client):
    resp = client.post("/t2-valet/api/stop")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "실행 중이 아닙니다."


def test_start_then_stop_cycle(client, monkeypatch):
    def fake_call(url, payload):
        return {"time": "2026-01-01 00:00:00", "type": "call", "status": 500,
                "body": "fake", "url": url, "payload": payload}
    monkeypatch.setattr(t2_valet, "do_single_call", fake_call)

    start = client.post("/t2-valet/api/start",
                        json={"name": "홍길동", "phone": "01012345678", "interval": 10})
    assert start.status_code == 200
    assert client.get("/t2-valet/api/logs").get_json()["running"] is True

    stop = client.post("/t2-valet/api/stop")
    assert stop.status_code == 200
    # 워커가 끝나기 전에 monkeypatch가 풀리면 실제 logs/에 쓸 수 있으므로 기다린다
    t2_valet.worker_thread.join(timeout=2)
    assert not t2_valet.worker_thread.is_alive()
    data = client.get("/t2-valet/api/logs").get_json()
    assert data["running"] is False
    statuses = [log["status"] for log in data["logs"]]
    assert "START" in statuses and "STOP" in statuses


def test_save_defaults_roundtrip(client):
    saved = client.post("/t2-valet/api/save-defaults",
                        json={"name": "홍길동", "phone": "01012345678", "interval": "30"})
    assert saved.status_code == 200
    data = client.get("/t2-valet/api/defaults").get_json()
    assert data["name"] == "홍길동"
    assert data["hasSavedData"] is True
    assert Path(t2_valet.USER_DATA_FILE).exists()


def test_clear_logs_empties_log(client):
    t2_valet.add_log({"time": "t", "type": "event", "status": "START", "body": "x"})
    assert client.get("/t2-valet/api/logs").get_json()["logs"] != []
    resp = client.post("/t2-valet/api/logs/clear")
    assert resp.status_code == 200
    assert client.get("/t2-valet/api/logs").get_json()["logs"] == []


def test_data_paths_are_project_root():
    # client fixture를 쓰지 않으므로 monkeypatch 전 원래 값이다.
    assert Path(t2_valet.USER_DATA_FILE).parent == PROJECT_ROOT
    assert Path(t2_valet.LOG_DIR).parent == PROJECT_ROOT


# ── 옛 경로 ──

def test_old_routes_are_gone(client):
    for path in ("/test", "/logs", "/defaults"):
        assert client.get(path).status_code == 404
