import random

import pytest

from services.gimpo.watch import (MAX_CONSECUTIVE_FAILURES, RETRY_BASE_CAP_SEC, exit_candidates, exit_note,
                                  jittered, retry_delay, short_time, wait_text)


@pytest.mark.parametrize('entry,desired,expected', [
    ('2026-10-03 11:00', '2026-10-06 18:00', ['2026-10-06 18:00', '2026-10-05 18:00', '2026-10-04 18:00']),
    # 입차 후 정확히 24시간인 D−2는 포함한다.
    ('2026-10-03 18:00', '2026-10-06 18:00', ['2026-10-06 18:00', '2026-10-05 18:00', '2026-10-04 18:00']),
    # 입차 후 24시간 미만인 D−2는 제외한다.
    ('2026-10-03 18:10', '2026-10-06 18:00', ['2026-10-06 18:00', '2026-10-05 18:00']),
    ('2026-10-03 11:00', '2026-10-04 18:00', ['2026-10-04 18:00']),
    # 요청 기간이 24시간 미만이어도 D는 포함한다.
    ('2026-10-03 11:00', '2026-10-03 15:00', ['2026-10-03 15:00']),
])
def test_exit_candidates(entry, desired, expected):
    assert exit_candidates(entry, desired) == expected


def test_jittered_uses_triangular_arguments_in_order():
    calls = []
    class Recorder:
        def triangular(self, low, high, mode):
            calls.append((low, high, mode))
            return 7.0
    assert jittered(60, Recorder()) == 67.0
    assert calls == [(0, 30.0, 9.0)]


@pytest.mark.parametrize('interval', [30, 60, 3600])
def test_jittered_stays_between_one_and_one_and_half_times(interval):
    rng = random.Random(1)
    values = [jittered(interval, rng) for _ in range(2000)]
    assert all(interval <= v <= interval * 1.5 for v in values)
    # 삼각분포(0, 0.5i, 0.15i)의 평균은 (0 + 0.5 + 0.15) / 3 × i ≈ 0.2167i 이다.
    assert abs(sum(values) / len(values) - interval * (1 + 0.65 / 3)) < interval * 0.01


def test_jittered_is_the_same_distribution_every_time():
    # 대기 분포는 회차와 무관하다: 동일 난수열이면 동일 값을 반환한다.
    a, b = random.Random(5), random.Random(5)
    assert [jittered(30, a) for _ in range(50)] == [jittered(30, b) for _ in range(50)]


@pytest.mark.parametrize('j,failures,expected', [
    (40, 1, 40), (40, 2, 80), (40, 3, 160), (40, 4, 320),
    (200, 3, 600), (200, 4, 600),          # 기본 상한 600초
    (600, 1, 600), (600, 4, 600),          # j가 600이면 j
    (900, 1, 900), (900, 4, 900),          # j가 600보다 크면 j를 보존한다
    (5400, 2, 5400),                       # 최대 간격 3600초의 최대 j
])
def test_retry_delay(j, failures, expected):
    assert retry_delay(j, failures) == expected
    assert retry_delay(j, failures) >= j


def test_constants_and_text():
    assert (MAX_CONSECUTIVE_FAILURES, RETRY_BASE_CAP_SEC) == (5, 600)
    assert short_time('2026-10-06 18:00') == '10/06 18:00'
    assert wait_text(36.5) == '약 36초' and wait_text(36.51) == '약 37초'
    assert exit_note('2026-10-06 18:00', '2026-10-06 18:00') is None
    assert exit_note('2026-10-06 18:00', '2026-10-04 18:00') == '원하는 출차(10/06 18:00)보다 2일 이릅니다'
