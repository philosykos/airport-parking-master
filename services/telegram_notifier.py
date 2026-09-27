"""Telegram transport. Never exposes URL, credentials or raw error responses."""
from dataclasses import dataclass
from typing import Protocol

import requests

from services.notification_config import TelegramSettings


@dataclass(frozen=True)
class Delivery:
    status: str
    message_id: int | None = None
    error: str | None = None
    retry_after: int = 0


class Notifier(Protocol):
    @property
    def enabled(self) -> bool: ...
    @property
    def configured(self) -> bool: ...
    @property
    def credentials_configured(self) -> bool: ...
    def send(self, text: str, reply_to: int | None = None) -> Delivery: ...


class TelegramNotifier:
    def __init__(self, settings: TelegramSettings, transport=None):
        self.settings = settings
        self._transport = transport or requests.Session()

    @property
    def enabled(self):
        return self.settings.enabled

    @property
    def configured(self):
        return self.settings.configured

    @property
    def credentials_configured(self):
        return self.settings.credentials_configured

    def send(self, text, reply_to=None):
        if not self.settings.enabled:
            return Delivery("DISABLED", error="텔레그램 알림이 꺼져 있습니다.")
        if not self.configured:
            return Delivery("FAILED", error="봇 토큰과 수신자 ID를 설정해주세요.")
        payload = {"chat_id": self.settings.chat_id, "text": text}
        if reply_to:
            # Telegram delivers normally when the original message was deleted.
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        try:
            response = self._transport.post(f"https://api.telegram.org/bot{self.settings.token}/sendMessage",
                                            json=payload, timeout=(5, 15))
            try:
                data = response.json()
            except (ValueError, TypeError):
                return Delivery("UNKNOWN", error="알림 전송 결과를 확인할 수 없습니다.")
            if not isinstance(data, dict):
                return Delivery("UNKNOWN", error="텔레그램 응답을 확인할 수 없습니다.")
            if response.status_code == 200 and data.get("ok") is True:
                message_id = data.get("result", {}).get("message_id")
                if type(message_id) is int:
                    return Delivery("SENT", message_id=message_id)
                return Delivery("UNKNOWN", error="알림 전송 결과를 확인할 수 없습니다.")
            code = data.get("error_code", response.status_code)
            if code == 429:
                delay = data.get("parameters", {}).get("retry_after", 30)
                return Delivery("RETRYING", error="텔레그램 전송이 일시적으로 제한되었습니다.", retry_after=delay if type(delay) is int and delay > 0 else 30)
            if type(code) is int and code >= 500:
                return Delivery("RETRYING", error="텔레그램 서버에 일시적인 오류가 발생했습니다.", retry_after=5)
            return Delivery("FAILED", error="봇 토큰과 수신자 ID를 확인한 뒤 테스트 알림을 보내주세요.")
        except requests.RequestException:
            return Delivery("UNKNOWN", error="알림 전송 결과를 확인할 수 없습니다. 텔레그램에서 수신 여부를 확인해주세요.")


class DeliveryPolicy:
    """Shared retry decision. Scheduling/lifetime remain owned by each queue."""
    def __init__(self, max_attempts=3):
        self.max_attempts = max_attempts

    def decide(self, result, attempt):
        status = result.status
        if status == "RETRYING" and attempt >= self.max_attempts:
            status = "FAILED"
        return status, max(result.retry_after, 5 * 2 ** (attempt - 1))
