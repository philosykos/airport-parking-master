import pytest

from services.t2 import valet as t2_valet

POST_PATHS = ["/t2-valet/api/stop", "/t2-valet/api/logs/clear", "/t2-valet/api/start",
              "/t2-valet/api/test", "/t2-valet/api/save-defaults"]


def seed_log():
    t2_valet.log_store.append({"time": "t", "type": "event", "status": "START", "body": "x"})


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8080", "localhost.evil.example"])
def test_untrusted_host_is_rejected(client, host):
    assert client.get("/t2-valet/api/defaults", headers={"Host": host}).status_code == 400


@pytest.mark.parametrize("host", ["localhost:8080", "127.0.0.1:8080"])
def test_local_hosts_are_served(client, host):
    assert client.get("/t2-valet/api/defaults", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Origin": "http://localhost:3000"},  # 같은 호스트라도 포트가 다르면 다른 출처
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "cross-site", "Origin": "http://localhost"},  # Sec-Fetch-Site가 우선
])
@pytest.mark.parametrize("path", POST_PATHS)
def test_cross_site_post_is_rejected(client, path, headers):
    resp = client.post(path, data="x=1", content_type="application/x-www-form-urlencoded", headers=headers)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "다른 사이트에서 온 요청은 받지 않습니다."


def test_cross_site_post_has_no_side_effect(client):
    seed_log()
    client.post("/t2-valet/api/logs/clear", headers={"Origin": "https://evil.example"})
    assert client.get("/t2-valet/api/logs").get_json()["logs"] != []


@pytest.mark.parametrize("host, headers", [
    ("localhost", {"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"}),
    ("localhost:8080", {"Origin": "http://localhost:8080"}),
    ("127.0.0.1:8080", {"Origin": "http://127.0.0.1:8080"}),
    ("localhost", {}),  # curl 등 브라우저 밖 클라이언트
    ("localhost", {"Sec-Fetch-Site": "none"}),  # 주소창 입력 등 사용자가 직접 연 요청
])
def test_same_origin_post_is_allowed(client, host, headers):
    seed_log()
    resp = client.post("/t2-valet/api/logs/clear", headers={"Host": host, **headers})
    assert resp.status_code == 200


def test_get_is_not_origin_checked(client):
    resp = client.get("/t2-valet/api/logs", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 200


@pytest.mark.parametrize("path", ["/", "/t2-valet/", "/t2-valet/api/logs"])
def test_common_security_headers(client, path):
    headers = client.get(path).headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "same-origin"
