"""서비스별 설정 파일(config/<서비스>.toml) 읽기와 공용 검증 도우미."""
import tomllib
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class ConfigError(Exception):
    pass


def config_label(service_name):
    return f"config/{service_name}.toml"


def load_toml(service_name):
    path = CONFIG_DIR / f"{service_name}.toml"
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path}: 설정 파일이 없습니다") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: TOML 문법 오류 — {e}") from None


def fail(label, key, reason):
    raise ConfigError(f"{label}: {key} — {reason}")


def require_table(raw, name, label):
    table = raw.get(name)
    if not isinstance(table, dict):
        fail(label, name, "테이블이 없습니다")
    return table


def reject_unknown(table, allowed, prefix, label):
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        fail(label, prefix + unknown[0], "알 수 없는 키입니다")


def find_legacy_env_keys(env_path, keys):
    """.env에 남아 있는 옛 키 이름을 파일에 나온 순서대로 돌려준다."""
    try:
        with open(env_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    found = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if name in keys and name not in found:
            found.append(name)
    return found
