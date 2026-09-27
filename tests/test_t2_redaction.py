import json
from pathlib import Path

import pytest

from services import t2_valet


@pytest.mark.parametrize("value, head, tail, expected", [
    ("홍길동", 1, 0, "홍**"),
    ("홍", 1, 0, "*"),
    ("", 1, 0, ""),
    ("01012345678", 3, 4, "010****5678"),
    ("12가3456", 3, 0, "12가****"),
])
def test_mask(value, head, tail, expected):
    assert t2_valet.mask(value, head, tail) == expected


def test_call_log_masks_personal_data(client, monkeypatch):
    sent = {}

    class Resp:
        status_code = 500
        text = '{"message": "010-1234-5678 확인"}'

    def fake_post(url, **kwargs):
        sent.update(kwargs["json"])
        return Resp()
    monkeypatch.setattr(t2_valet.http_requests, "post", fake_post)

    resp = client.post("/t2-valet/api/test",
                       json={"name": "홍길동", "phone": "01012345678", "carNumber": "12가3456"})
    # 예약 API에는 원본을 보낸다
    assert sent["name"] == "홍길동" and sent["phone"] == "01012345678" and sent["carNumber"] == "12가3456"

    raw = Path(t2_valet.log_store.path).read_text(encoding="utf-8")
    for secret in ("홍길동", "01012345678", "010-1234-5678", "12가3456"):
        assert secret not in raw
    for entry in (resp.get_json()["result"], json.loads(raw.splitlines()[-1])):
        assert entry["payload"]["name"] == "홍**"
        assert entry["payload"]["phone"] == "010****5678"
        assert entry["payload"]["carNumber"] == "12가****"
        assert "010******5678" in entry["body"]


def test_call_log_masks_personal_data_echoed_in_response_body(client, monkeypatch):
    class Resp:
        status_code = 500
        text = '{"name": "홍길동", "carNumber": "12가3456", "phone": "01012345678"}'

    monkeypatch.setattr(t2_valet.http_requests, "post", lambda url, **kwargs: Resp())

    resp = client.post("/t2-valet/api/test",
                       json={"name": "홍길동", "phone": "01012345678", "carNumber": "12가3456"})

    raw = Path(t2_valet.log_store.path).read_text(encoding="utf-8")
    for secret in ("홍길동", "01012345678", "12가3456"):
        assert secret not in raw
        assert secret not in resp.get_data(as_text=True)

    entry = resp.get_json()["result"]
    assert "홍**" in entry["body"]
    assert "12가****" in entry["body"]
    assert "010****5678" in entry["body"]


def test_do_single_call_masks_phone_before_truncating(monkeypatch):
    class Resp:
        status_code = 200
        text = "x" * 1995 + "01012345678"

    monkeypatch.setattr(t2_valet.http_requests, "post", lambda url, **kwargs: Resp())
    entry = t2_valet.do_single_call("https://example.invalid", {"name": "", "phone": "", "carNumber": ""})
    assert "0101234" not in entry["body"]
