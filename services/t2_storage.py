"""T2 로컬 파일 저장: 예약 기본값(JSON)과 실행 로그(JSON Lines).

두 파일 모두 개인정보를 담으므로 소유자만 읽고 쓰게(0600) 만든다.
이전 버전이 0644로 만든 파일도 읽거나 쓸 때 권한을 좁힌다.
"""
import collections
import json
import os
import threading

PRIVATE_MODE = 0o600


def _open(path, flags, mode="r"):
    fd = os.open(path, flags, PRIVATE_MODE)
    try:
        # 권한은 소유자만 바꿀 수 있다. sudo로 실행해 root가 만든 파일 등은 그대로 두고 연다.
        if os.fstat(fd).st_uid == os.getuid():
            os.fchmod(fd, PRIVATE_MODE)
        return os.fdopen(fd, mode, encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise


def write_private(path, text):
    """임시 파일에 쓴 뒤 바꿔 끼운다. 쓰다가 멈춰도 원래 파일이 깨지지 않는다."""
    tmp = f"{path}.tmp"
    with _open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, "w") as f:
        f.write(text)
    os.replace(tmp, path)


class UserDataStore:
    def __init__(self, path):
        self.path = path

    def load(self):
        """저장된 기본값 사전. 없거나 깨졌거나 사전이 아니면 None."""
        try:
            with _open(self.path, os.O_RDONLY) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, data):
        write_private(self.path, json.dumps(data, ensure_ascii=False, indent=2))


class LogStore:
    """최근 max_entries건을 돌려준다. 파일이 max_bytes를 넘으면 최근 max_entries건만 남긴다."""

    def __init__(self, path, max_entries=500, max_bytes=1_000_000):
        self.path = path
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._lock = threading.Lock()  # 워커 스레드와 요청 스레드가 함께 쓴다

    def append(self, entry):
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            with _open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, "a") as f:
                f.write(line)
            if os.path.getsize(self.path) > self.max_bytes:
                write_private(self.path, "".join(self._tail()))

    def recent(self):
        with self._lock:
            lines = self._tail()
        entries = []
        for line in lines:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 이전 버전이 남긴 깨진 줄은 건너뛴다
        return entries

    def clear(self):
        with self._lock:
            write_private(self.path, "")

    def _tail(self):
        try:
            with _open(self.path, os.O_RDONLY) as f:
                return list(collections.deque((line for line in f if line.strip()), maxlen=self.max_entries))
        except FileNotFoundError:
            return []
