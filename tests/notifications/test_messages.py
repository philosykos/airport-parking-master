from datetime import datetime
from zoneinfo import ZoneInfo

from services.notifications.messages import ReservationMessages

SEOUL = ZoneInfo('Asia/Seoul')
SUMMARY = {'parkingName': '국내선 제2주차장 주차타워 2, 3층', 'entryAt': '2026-10-28 10:00', 'exitAt': '2026-10-28 18:00',
           'calculateAmt': 8000, 'discountAmt': 0, 'depositAmt': 10000}
READY_JOB = {'id': 'GMP-1', 'summary': SUMMARY, 'reason': '화면 문구',
             'availabilityCheckedAt': datetime(2026, 9, 28, 2, 24, 38, tzinfo=SEOUL).timestamp()}


def test_gimpo_ready_message():
    text = ReservationMessages.gimpo({'kind': 'READY', 'round': 2}, READY_JOB).render()
    assert text == ('[김포공항 국내선 주차] 결제 대기 — 예약 미완료\n\n'
                    '주차장: 국내선 제2주차장 주차타워 2, 3층\n입차: 2026-10-28 10:00\n출차: 2026-10-28 18:00\n'
                    '예상 주차요금: 8,000원\n예약 보증금: 10,000원\n확인 시각: 2026-09-28 02:24\n\n'
                    '예약 프로그램이 실행 중인 PC의 공항 예약창에서 결제해주세요.')


def test_gimpo_correction_messages_follow_cause_not_job_reason():
    expired = ReservationMessages.gimpo({'kind': 'CORRECTION', 'cause': 'EXPIRED', 'round': 1}, READY_JOB).render()
    assert expired == ('[김포공항 국내선 주차] 결제 대기 시간 초과\n\n'
                       '결제 대기 시간이 지나 앞서 보낸 결제 안내로는 예약할 수 없습니다. 예약하려면 PC에서 빈자리를 다시 조회해주세요.')
    stopped = ReservationMessages.gimpo({'kind': 'CORRECTION', 'cause': 'INTERRUPTED', 'round': 1}, READY_JOB).render()
    assert stopped == ('[김포공항 국내선 주차] 결제 대기 중단\n\n'
                       '공항 예약창 연결이 끊겨 앞서 보낸 결제 안내로는 예약할 수 없습니다. 예약하려면 PC에서 빈자리를 다시 조회해주세요.')
    assert '화면 문구' not in expired + stopped


def test_t2_completed_message():
    payload = {'departingAt': '2026-10-03 11:00:00', 'arrivedAt': '2026-10-06 18:00:00'}
    text = ReservationMessages.t2_completed(payload, datetime(2026, 10, 1, 9, 5, 30, tzinfo=SEOUL)).render()
    assert text == ('[인천공항 T2 발렛] 예약 완료\n\n입차: 2026-10-03 11:00\n출차: 2026-10-06 18:00\n확인 시각: 2026-10-01 09:05\n\n'
                    '공항 사이트에서 예약 내역을 확인해주세요.')


def test_test_message_has_no_empty_field_block():
    assert ReservationMessages.test('김포공항 국내선 주차').render() == '[김포공항 국내선 주차] 테스트 알림\n\n테스트용 알림 메세지입니다.'


def test_no_message_has_job_line_or_seconds_or_zone():
    texts = [ReservationMessages.gimpo({'kind': 'READY', 'round': 1}, READY_JOB).render(),
             ReservationMessages.t2_completed({}, datetime(2026, 10, 1, 9, 5, 30, tzinfo=SEOUL)).render(),
             ReservationMessages.test('T2').render()]
    for text in texts:
        assert '작업:' not in text and '(서울)' not in text and ':30' not in text
