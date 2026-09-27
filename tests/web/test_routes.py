import dataclasses
import re
import threading
from pathlib import Path

import pytest

import app as app_module
from services.t2 import valet as t2_valet

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    assert resp.get_json()["error"] == "예약 요청 간격은 10초 이상으로 입력해주세요."


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
    t2_valet.scheduler.thread.join(timeout=2)
    assert not t2_valet.scheduler.thread.is_alive()
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
    assert Path(t2_valet.user_store.path).exists()


def test_clear_logs_empties_log(client):
    t2_valet.log_store.append({"time": "t", "type": "event", "status": "START", "body": "x"})
    assert client.get("/t2-valet/api/logs").get_json()["logs"] != []
    resp = client.post("/t2-valet/api/logs/clear")
    assert resp.status_code == 200
    assert client.get("/t2-valet/api/logs").get_json()["logs"] == []


def test_data_paths_are_project_root():
    # client fixture를 쓰지 않으므로 monkeypatch 전 원래 값이다.
    assert Path(t2_valet.user_store.path).parent == PROJECT_ROOT
    assert Path(t2_valet.log_store.path).parent == PROJECT_ROOT / "logs"


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


def test_landing_gmp_card_is_enabled(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/gimpo-parking/"' in html
    assert 'aria-disabled="true"' not in html


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


def test_restart_during_inflight_call_keeps_one_worker(client, monkeypatch):
    entered, release, names = threading.Event(), threading.Event(), []

    def fake_call(url, payload):
        names.append(payload["name"])
        entered.set()
        release.wait(5)
        return {"time": "t", "type": "call", "status": 500, "body": "fake", "url": url, "payload": payload}
    monkeypatch.setattr(t2_valet, "do_single_call", fake_call)

    body = {"phone": "01012345678", "interval": 10}
    try:
        assert client.post("/t2-valet/api/start", json={**body, "name": "이전"}).status_code == 200
        assert entered.wait(2)
        old = t2_valet.scheduler.thread
        assert client.post("/t2-valet/api/stop").status_code == 200
        entered.clear()
        assert client.post("/t2-valet/api/start", json={**body, "name": "새로"}).status_code == 200
        assert entered.wait(2)
        release.set()
        old.join(timeout=2)
        assert not old.is_alive()
        assert names == ["이전", "새로"]
        assert client.get("/t2-valet/api/logs").get_json()["running"] is True
    finally:
        release.set()  # 중간에 실패해도 워커를 풀어 픽스처 정리 전에 끝나게 한다


# ── 입력 검사 (t2_input) ──

@pytest.mark.parametrize("path", ["/t2-valet/api/test", "/t2-valet/api/start"])
@pytest.mark.parametrize("raw", ["null", "[]", '"x"', "{bad"])
def test_non_object_json_is_400(client, path, raw):
    resp = client.post(path, data=raw, content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "요청 본문은 JSON 객체여야 합니다."


def test_start_rejects_non_integer_interval(client):
    resp = client.post("/t2-valet/api/start",
                       json={"name": "홍길동", "phone": "01012345678", "interval": "abc"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "예약 요청 간격은 정수로 입력해주세요."
    assert client.get("/t2-valet/api/logs").get_json()["running"] is False


def test_test_call_rejects_bad_field(client):
    resp = client.post("/t2-valet/api/test",
                       json={"name": "홍길동", "phone": "01012345678", "carBrand": {"x": 1}})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "제조사: 문자열이어야 합니다."


def test_client_cannot_override_fixed_payload(client, monkeypatch):
    captured = {}

    def fake_call(url, payload):
        captured.update(payload)
        return {"time": "t", "type": "call", "status": 500, "body": "fake", "url": url, "payload": payload}
    monkeypatch.setattr(t2_valet, "do_single_call", fake_call)
    client.post("/t2-valet/api/test",
                json={"name": "홍길동", "phone": "01012345678", "carType": "PREMIUM", "isCrew": True})
    assert captured["carType"] == t2_valet.CONFIG.payload["carType"]
    assert captured["isCrew"] == t2_valet.CONFIG.payload["isCrew"]


def test_save_defaults_rejects_bad_types(client):
    resp = client.post("/t2-valet/api/save-defaults", json={"name": ["홍길동"]})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "예약자명: 문자열이어야 합니다."
    assert client.get("/t2-valet/api/defaults").get_json()["hasSavedData"] is False


@pytest.mark.parametrize("raw", ["{}", "null"])
def test_save_defaults_rejects_empty(client, raw):
    resp = client.post("/t2-valet/api/save-defaults", data=raw, content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "데이터가 없습니다."


def test_save_defaults_normalizes_interval(client):
    client.post("/t2-valet/api/save-defaults", json={"name": "홍길동", "interval": 20})
    assert client.get("/t2-valet/api/defaults").get_json()["interval"] == "20"


@pytest.mark.parametrize("content", ["[1, 2]", '"text"', "{broken", ""])
def test_defaults_ignores_unusable_saved_file(client, content):
    Path(t2_valet.user_store.path).write_text(content, encoding="utf-8")
    resp = client.get("/t2-valet/api/defaults")
    assert resp.status_code == 200
    assert resp.get_json()["hasSavedData"] is False


def test_t2_page_does_not_load_enhancement_js(client):
    html = client.get("/t2-valet/").get_data(as_text=True)
    assert "enhancement.js" not in html
    assert not (PROJECT_ROOT / "static/js/enhancement.js").exists()


# ── 인라인 이벤트 제거·CSP·SRI ──

PRETENDARD_URL = "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css"
PRETENDARD_SRI = "sha384-SN6A48CJQjx946+DRb8wsoifC4a8ur9ZS6R+HCTgnBHOKCa6GLXAR3Qn8d1jztxg"


def test_frontend_has_no_inline_event_handlers():
    files = [*(PROJECT_ROOT / "templates").rglob("*.html"), *(PROJECT_ROOT / "static/js").rglob("*.js")]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\son[a-z]+\s*=\s*[\"']", text), path


def test_t2_page_sends_csp(client):
    csp = client.get("/t2-valet/").headers["Content-Security-Policy"]
    directives = dict(part.strip().split(" ", 1) for part in csp.split(";"))
    assert directives["script-src"] == "'self'"
    assert directives["object-src"] == "'none'"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["connect-src"] == "'self'"


@pytest.mark.parametrize("path", ["/", "/t2-valet/"])
def test_pretendard_css_is_integrity_pinned(client, path):
    html = client.get(path).get_data(as_text=True)
    tag = re.search(r"<link[^>]*pretendard[^>]*>", html).group(0)
    assert f'href="{PRETENDARD_URL}"' in tag
    assert f'integrity="{PRETENDARD_SRI}"' in tag
    assert "crossorigin" in tag


# ── 공통 화면 문구·상태 표시 ──

def test_gimpo_page_drops_intro_copy_and_shows_status_only_in_header(client):
    html = client.get("/gimpo-parking/").get_data(as_text=True)
    assert "결제는 이 PC에" not in html
    assert "자동 저장됩니다" not in html
    assert 'id="header-status"' in html
    assert 'id="state"' not in html
    assert 'id="status-badge"' not in html
    assert ">예약 요약<" in html and "진행 상황" not in html
    assert ">실행 로그<" in html and "작업 로그" not in html


@pytest.mark.parametrize("path", ["/", "/t2-valet/", "/gimpo-parking/"])
def test_settings_button_uses_gear_symbol(client, path):
    html = client.get(path).get_data(as_text=True)
    button = re.search(r'<button[^>]*id="open-settings".*?</button>', html, re.S).group(0)
    assert re.search(r'<span class="material-symbols-outlined"[^>]*>settings</span>', button)
    assert "<svg" not in button
