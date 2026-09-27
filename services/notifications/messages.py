"""Shared, plain-text reservation message format and service-specific factories."""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class NotificationMessage:
    service: str
    title: str
    fields: tuple[tuple[str, str], ...]
    instruction: str
    reference: str

    def render(self):
        lines = [f"[{self.service}] {self.title}", ""]
        lines.extend(f"{label}: {value}" for label, value in self.fields)
        return "\n".join([*lines, "", self.instruction, "", f"작업: {self.reference}"])


class ReservationMessages:
    @staticmethod
    def _date(value):
        try:
            date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if date.tzinfo:
                date = date.astimezone(SEOUL)
            return date.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return "공식 예약 내역에서 확인"

    @classmethod
    def t2_completed(cls, payload, reference, detected_at=None):
        detected_at = detected_at or datetime.now(SEOUL)
        return NotificationMessage("인천공항 T2 발렛", "예약 완료", (
            ("입차", cls._date(payload.get("departingAt"))),
            ("출차", cls._date(payload.get("arrivedAt"))),
            ("확인 시각", detected_at.astimezone(SEOUL).strftime("%Y-%m-%d %H:%M:%S") + " (서울)")),
            "공항 사이트에서 예약 내역을 확인해주세요.", reference)

    @staticmethod
    def gimpo(event, job):
        reference = f'{job["id"]} / 안내 {event["round"]}회차'
        if event["kind"] == "CORRECTION":
            return NotificationMessage("김포공항 국내선 주차", "결제 안내 변경", (("현재 상태", job["reason"]),),
                                       "이전 결제 대기 안내는 더 이상 유효하지 않습니다. 결제를 진행했다면 공항 사이트에서 예약 결과를 확인해주세요.", reference)
        summary = job["summary"]
        checked = datetime.fromtimestamp(job["availabilityCheckedAt"], SEOUL).strftime("%Y-%m-%d %H:%M:%S")
        return NotificationMessage("김포공항 국내선 주차", "결제 대기 — 예약 미완료", (
            ("주차장", summary["parkingName"]), ("입차", summary["entryAt"]), ("출차", summary["exitAt"]),
            ("예상 주차요금", f'{summary["calculateAmt"]:,}원'), ("예약 보증금", f'{summary["depositAmt"]:,}원'),
            ("잔여석 조회 시각", checked + " (서울)")),
            '예약 프로그램이 실행 중인 PC의 공항 예약창에서 결제해주세요.\n'
            '아직 자리가 확보되지 않았습니다. 대기 시간이 지나면 예약 화면에서 ‘다시 준비’를 눌러주세요.', reference)

    @staticmethod
    def test(service):
        return NotificationMessage(service, "테스트 알림", (), "텔레그램 알림이 연결되었습니다.", "TEST")
