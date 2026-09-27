"""Shared, plain-text reservation message format and service-specific factories."""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class NotificationMessage:
    service: str
    title: str
    fields: tuple[tuple[str, str], ...] = ()
    instruction: str = ""

    def render(self):
        # 항목이 없으면 빈 줄 없이 제목과 안내만 둔다. 중복 전송 방지는 이벤트 ID가 맡는다.
        lines = [f"[{self.service}] {self.title}"]
        if self.fields:
            lines += ["", *(f"{label}: {value}" for label, value in self.fields)]
        if self.instruction:
            lines += ["", self.instruction]
        return "\n".join(lines)


GIMPO = "김포공항 국내선 주차"
# 결제 대기 알림을 무효로 만든 원인별 변경 알림(store.CORRECTION_CAUSES의 값)
CORRECTIONS = {
    "EXPIRED": ("결제 대기 시간 초과",
                "결제 대기 시간이 지나 앞서 보낸 결제 안내로는 예약할 수 없습니다. 예약하려면 PC에서 빈자리를 다시 조회해주세요."),
    "INTERRUPTED": ("결제 대기 중단",
                    "공항 예약창 연결이 끊겨 앞서 보낸 결제 안내로는 예약할 수 없습니다. 예약하려면 PC에서 빈자리를 다시 조회해주세요."),
}


class ReservationMessages:
    # _date는 지금 코드 그대로 둔다
    @staticmethod
    def _date(value):
        try:
            date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if date.tzinfo:
                date = date.astimezone(SEOUL)
            return date.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return "공식 예약 내역에서 확인"

    @staticmethod
    def _minute(moment):
        return moment.astimezone(SEOUL).strftime("%Y-%m-%d %H:%M")

    @classmethod
    def t2_completed(cls, payload, detected_at=None):
        return NotificationMessage("인천공항 T2 발렛", "예약 완료", (
            ("입차", cls._date(payload.get("departingAt"))),
            ("출차", cls._date(payload.get("arrivedAt"))),
            ("확인 시각", cls._minute(detected_at or datetime.now(SEOUL)))),
            "공항 사이트에서 예약 내역을 확인해주세요.")

    @classmethod
    def gimpo(cls, event, job):
        if event["kind"] == "CORRECTION":
            # 이전 버전이 만든 CORRECTION(원인 없음)은 중단 안내로 보낸다.
            title, instruction = CORRECTIONS.get(event.get("cause"), CORRECTIONS["INTERRUPTED"])
            return NotificationMessage(GIMPO, title, (), instruction)
        summary = job["summary"]
        return NotificationMessage(GIMPO, "결제 대기 — 예약 미완료", (
            ("주차장", summary["parkingName"]), ("입차", summary["entryAt"]), ("출차", summary["exitAt"]),
            ("예상 주차요금", f'{summary["calculateAmt"] - summary.get("discountAmt", 0):,}원'),
            ("예약 보증금", f'{summary["depositAmt"]:,}원'),
            ("확인 시각", cls._minute(datetime.fromtimestamp(job["availabilityCheckedAt"], SEOUL)))),
            "예약 프로그램이 실행 중인 PC의 공항 예약창에서 결제해주세요.")

    @staticmethod
    def test(service):
        return NotificationMessage(service, "테스트 알림", (), "테스트용 알림 메세지입니다.")
