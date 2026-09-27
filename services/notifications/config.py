"""Shared notification policy and credentials for every airport."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from services.config import config_label, fail, load_toml, reject_unknown, require_table

ENV_PATH = Path(__file__).resolve().parents[2] / '.env'


@dataclass(frozen=True)
class NotificationConfig:
    max_attempts: int

    @classmethod
    def parse(cls, raw):
        label = config_label("notifications")
        reject_unknown(raw, {"telegram"}, "", label)
        telegram = require_table(raw, "telegram", label)
        reject_unknown(telegram, {"max_attempts"}, "telegram.", label)
        attempts = telegram.get("max_attempts")
        if type(attempts) is not int or not 1 <= attempts <= 3:
            fail(label, "telegram.max_attempts", "1~3 범위의 정수여야 합니다")
        return cls(attempts)


@dataclass(frozen=True)
class TelegramSettings:
    enabled: bool = False
    token: str = field(default="", repr=False)
    chat_id: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls):
        # Read only common notification settings; never mutate process configuration.
        try:
            with ENV_PATH.open(encoding='utf-8') as stream:
                values = dotenv_values(stream=stream, interpolate=False)
        except (OSError, UnicodeError):
            values = {}
        def value(key):
            return (os.environ.get(key, values.get(key)) or '').strip()
        return cls(value('TELEGRAM_ALARM_ENABLED').lower() == 'true',
                   value('TELEGRAM_BOT_TOKEN'), value('TELEGRAM_CHAT_ID'))

    @property
    def credentials_configured(self):
        return bool(self.token and self.chat_id)

    @property
    def configured(self):
        return self.enabled and self.credentials_configured


CONFIG = NotificationConfig.parse(load_toml("notifications"))
