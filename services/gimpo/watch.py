"""감시 모드 규칙: 출차 후보, 대기 간격, 오류 재시도 대기, 표시 문구. 브라우저·저장소를 모르는 순수 함수."""
from datetime import datetime, timedelta

MAX_CONSECUTIVE_FAILURES = 5
RETRY_BASE_CAP_SEC = 600
_FORMAT = "%Y-%m-%d %H:%M"


def _parse(value):
    return datetime.strptime(value, _FORMAT)


def exit_candidates(entry_at, exit_at):
    """원하는 출차 D와 D−1일·D−2일 중 입차부터 24시간 이상인 것을 순서대로 돌려준다. D는 항상 첫 후보다."""
    entry, desired = _parse(entry_at), _parse(exit_at)
    result = [exit_at]
    for days in (1, 2):
        candidate = desired - timedelta(days=days)
        if candidate - entry >= timedelta(hours=24):
            result.append(candidate.strftime(_FORMAT))
    return result


def jittered(interval, rng):
    """입력 간격을 최소 대기로 두고 삼각분포(최솟값 0, 최댓값 0.5배, 최빈값 0.15배)의 지연을 더한다."""
    return interval + rng.triangular(0, 0.5 * interval, 0.15 * interval)


def retry_delay(j, failures):
    """연속 실패 수만큼 j를 두 배씩 늘린다. 기본 상한은 600초이고, j가 600초를 넘으면 상한 대신 j 자체를 보존한다."""
    return min(j * 2 ** (failures - 1), max(RETRY_BASE_CAP_SEC, j))


def short_time(value):
    return _parse(value).strftime("%m/%d %H:%M")


def wait_text(seconds):
    return f"약 {round(seconds)}초"


def exit_note(requested, actual):
    """실제 예약할 출차가 원하는 출차보다 이르면 며칠 이른지 알리는 문구. 같으면 None."""
    if requested == actual:
        return None
    days = (_parse(requested) - _parse(actual)).days
    return f"원하는 출차({short_time(requested)})보다 {days}일 이릅니다"
