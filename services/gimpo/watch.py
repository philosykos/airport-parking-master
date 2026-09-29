"""감시 모드 정책: 출차 후보, 대기 간격, 재시도 대기, 표시 문구. 브라우저·저장소와 독립된 순수 함수."""
from datetime import datetime, timedelta

MAX_CONSECUTIVE_FAILURES = 5
RETRY_BASE_CAP_SEC = 600
_FORMAT = "%Y-%m-%d %H:%M"


def _parse(value):
    return datetime.strptime(value, _FORMAT)


def exit_candidates(entry_at, exit_at):
    """출차 후보 목록: 요청 출차 D, D−1일, D−2일 중 입차 후 24시간 이상인 후보. D는 항상 첫 후보다."""
    entry, desired = _parse(entry_at), _parse(exit_at)
    result = [exit_at]
    for days in (1, 2):
        candidate = desired - timedelta(days=days)
        if candidate - entry >= timedelta(hours=24):
            result.append(candidate.strftime(_FORMAT))
    return result


def jittered(interval, rng):
    """회차 대기 시간: 입력 간격에 삼각분포(최솟값 0, 최댓값 0.5배, 최빈값 0.15배) 지연을 더한다."""
    return interval + rng.triangular(0, 0.5 * interval, 0.15 * interval)


def retry_delay(j, failures):
    """재시도 대기 시간: 연속 실패마다 j를 2배씩 늘리며 기본 상한은 600초, j가 600초를 넘으면 j를 적용한다."""
    return min(j * 2 ** (failures - 1), max(RETRY_BASE_CAP_SEC, j))


def short_time(value):
    return _parse(value).strftime("%m/%d %H:%M")


def wait_text(seconds):
    return f"약 {round(seconds)}초"


def exit_note(requested, actual):
    """앞당긴 출차 안내 문구. 신청 출차가 요청 출차와 같으면 None."""
    if requested == actual:
        return None
    days = (_parse(requested) - _parse(actual)).days
    return f"원하는 출차({short_time(requested)})보다 {days}일 이릅니다"
