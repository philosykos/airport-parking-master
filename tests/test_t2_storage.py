import json
import os
import stat
from pathlib import Path

import pytest

from services.t2.storage import LogStore, UserDataStore, write_private


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_user_data_roundtrip_is_owner_only(tmp_path):
    store = UserDataStore(str(tmp_path / "user_data.json"))
    assert store.load() is None
    store.save({"name": "홍길동"})
    assert store.load() == {"name": "홍길동"}
    assert mode(store.path) == 0o600


@pytest.mark.parametrize("content", ["[1, 2]", '"text"', "{broken", ""])
def test_unusable_user_data_loads_as_none(tmp_path, content):
    path = tmp_path / "user_data.json"
    path.write_text(content, encoding="utf-8")
    assert UserDataStore(str(path)).load() is None


def test_reading_tightens_legacy_permissions(tmp_path):
    data = tmp_path / "user_data.json"
    data.write_text('{"name": "a"}', encoding="utf-8")
    log = tmp_path / "api_call.log"
    log.write_text('{"n": 1}\n', encoding="utf-8")
    os.chmod(data, 0o644)
    os.chmod(log, 0o644)
    UserDataStore(str(data)).load()
    LogStore(str(log)).recent()
    assert mode(data) == 0o600
    assert mode(log) == 0o600


def test_write_private_leaves_no_temp_file(tmp_path):
    write_private(str(tmp_path / "f"), "x")
    assert [p.name for p in tmp_path.iterdir()] == ["f"]
    assert mode(tmp_path / "f") == 0o600


def test_log_append_recent_clear(tmp_path):
    store = LogStore(str(tmp_path / "api_call.log"))
    assert store.recent() == []
    store.append({"n": 1})
    store.append({"n": 2})
    assert store.recent() == [{"n": 1}, {"n": 2}]
    assert mode(store.path) == 0o600
    store.clear()
    assert store.recent() == []
    assert mode(store.path) == 0o600


def test_recent_returns_last_max_entries(tmp_path):
    store = LogStore(str(tmp_path / "api_call.log"), max_entries=3)
    for n in range(5):
        store.append({"n": n})
    assert [e["n"] for e in store.recent()] == [2, 3, 4]


def test_file_is_trimmed_past_max_bytes(tmp_path):
    store = LogStore(str(tmp_path / "api_call.log"), max_entries=3, max_bytes=200)
    for n in range(20):
        store.append({"n": n, "body": "y" * 100})
    lines = Path(store.path).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["n"] for line in lines] == [17, 18, 19]


def test_corrupt_lines_are_skipped(tmp_path):
    path = tmp_path / "api_call.log"
    path.write_text('not json\n\n{"n": 1}\n', encoding="utf-8")
    assert LogStore(str(path)).recent() == [{"n": 1}]


def test_files_owned_by_another_user_are_read_without_chmod(tmp_path, monkeypatch):
    # sudo로 실행해 root가 만든 파일처럼 소유자가 아니면 권한을 바꿀 수 없다(fchmod가 EPERM).
    # 권한은 그대로 두고, 읽을 수 있으면 읽는다.
    data = tmp_path / "user_data.json"
    data.write_text('{"name": "a"}', encoding="utf-8")
    log = tmp_path / "api_call.log"
    log.write_text('{"n": 1}\n', encoding="utf-8")
    monkeypatch.setattr(os, "getuid", lambda: os.stat(data).st_uid + 1)

    def refuse(fd, mode):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "fchmod", refuse)

    assert UserDataStore(str(data)).load() == {"name": "a"}
    assert LogStore(str(log)).recent() == [{"n": 1}]
