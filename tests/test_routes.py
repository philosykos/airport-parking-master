import dataclasses
import re
from pathlib import Path

import app as app_module
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


def test_test_call_never_reaches_network(client):
    # 설정 파일의 실제 URL이 아니라 테스트용 URL로 가고, 그마저도 외부로 나가지 않아야 한다
    resp = client.post("/t2-valet/api/test", json={"name": "홍길동", "phone": "01012345678"})
    assert resp.status_code == 200
    result = resp.get_json()["result"]
    assert result["url"] == "https://example.invalid/reserve"
    assert result["status"] == "ERROR"
    assert "테스트에서 외부 호출 차단" in result["body"]


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


# ── 랜딩 ──

def test_landing_lists_services(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'href="/t2-valet/"' in html
    assert "인천공항 제2터미널 발렛파킹" in html
    assert "김포공항 국내선 예약주차장" in html
    assert "<title>공항 주차 예약 서비스</title>" in html


def test_landing_gmp_card_is_disabled(client):
    html = client.get("/").get_data(as_text=True)
    match = re.search(r'<div class="service-card is-soon"[^>]*>', html)
    assert match, "김포 카드 여는 태그가 없다"
    tag = match.group(0)
    assert 'role="group"' in tag
    assert 'aria-disabled="true"' in tag
    assert "href" not in tag


def test_landing_does_not_load_t2_scripts(client):
    html = client.get("/").get_data(as_text=True)
    assert "js/app.js" not in html
    assert "js/datepicker.js" not in html


def test_t2_page_has_back_link(client):
    html = client.get("/t2-valet/").get_data(as_text=True)
    match = re.search(r'<a class="header-back"[^>]*>', html)
    assert match, "돌아가기 링크가 없다"
    tag = match.group(0)
    assert 'href="/"' in tag
    assert 'aria-label="서비스 선택으로 돌아가기"' in tag


# ── 설정 연결 ──

def test_test_call_uses_config(client, monkeypatch):
    cfg = dataclasses.replace(t2_valet.CONFIG,
                              payload={**t2_valet.CONFIG.payload, "carType": "PREMIUM"})
    monkeypatch.setattr(t2_valet, "CONFIG", cfg)
    captured = {}

    def fake_call(url, payload):
        captured.update(url=url, payload=payload)
        return {"time": "t", "type": "call", "status": 500, "body": "fake", "url": url, "payload": payload}
    monkeypatch.setattr(t2_valet, "do_single_call", fake_call)

    resp = client.post("/t2-valet/api/test", json={"name": "홍길동", "phone": "01012345678"})
    assert resp.status_code == 200
    assert captured["url"] == "https://example.invalid/reserve"
    assert captured["payload"]["carType"] == "PREMIUM"
    assert captured["payload"]["name"] == "홍길동"


def test_defaults_uses_config_interval(client, monkeypatch):
    monkeypatch.setattr(t2_valet, "CONFIG", dataclasses.replace(t2_valet.CONFIG, interval_sec=45))
    data = client.get("/t2-valet/api/defaults").get_json()
    assert data == {"interval": "45", "hasSavedData": False}


def test_saved_interval_overrides_config(client):
    client.post("/t2-valet/api/save-defaults",
                json={"name": "홍길동", "phone": "01012345678", "interval": "20"})
    assert client.get("/t2-valet/api/defaults").get_json()["interval"] == "20"


def test_warn_legacy_env_prints_keys(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("REQUEST_URL=https://x\nCAR_TYPE=BASIC\n", encoding="utf-8")
    app_module.warn_legacy_env(env)
    assert capsys.readouterr().out.strip() == (
        "[경고] .env의 REQUEST_URL, CAR_TYPE은 더 이상 읽지 않습니다. config/t2_valet.toml로 옮기세요."
    )


def test_warn_legacy_env_silent_without_legacy_keys(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\n", encoding="utf-8")
    app_module.warn_legacy_env(env)
    assert capsys.readouterr().out == ""
