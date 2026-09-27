"""In-memory notification transport for settings and delivery tests."""
from services.notifications.config import TelegramSettings
from services.notifications.telegram import Delivery


class TransportNotifier:
    def __init__(self, enabled=True, results=None):
        self.settings = TelegramSettings(enabled, 'token', 'chat')
        self.calls = []
        self.results = list(results or [])
    @property
    def enabled(self): return self.settings.enabled
    @property
    def configured(self): return self.settings.configured
    @property
    def credentials_configured(self): return self.settings.credentials_configured
    def send(self, message):
        self.calls.append(message)
        return self.results.pop(0) if self.results else Delivery('SENT', message_id=1)
