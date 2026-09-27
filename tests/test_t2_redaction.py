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
