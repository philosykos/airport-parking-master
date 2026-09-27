"""T2와 같은 TOML 로더를 사용하는 김포 서비스 설정."""
from dataclasses import dataclass
from pathlib import Path

from services.config import config_label, fail, load_toml, reject_unknown, require_table


@dataclass(frozen=True)
class GimpoConfig:
    interval_sec: int
    handoff_max_age_sec: int
    browser_timeout_sec: int
    directory: Path


def parse_config(raw):
    label = config_label("gimpo_parking")
    schema = {"request": {"interval_sec", "handoff_max_age_sec", "browser_timeout_sec"},
              "storage": {"directory"}}
    reject_unknown(raw, schema, "", label)
    tables = {}
    for name, keys in schema.items():
        tables[name] = require_table(raw, name, label)
        reject_unknown(tables[name], keys, name + ".", label)
    def integer(section, key, lo, hi):
        value = tables[section].get(key)
        if type(value) is not int or not lo <= value <= hi:
            fail(label, f"{section}.{key}", f"{lo}~{hi} 범위의 정수여야 합니다")
        return value
    directory = tables["storage"].get("directory")
    if not isinstance(directory, str) or not directory.strip():
        fail(label, "storage.directory", "빈 값이 아닌 경로여야 합니다")
    root = Path(__file__).resolve().parents[2]
    return GimpoConfig(integer("request", "interval_sec", 30, 3600),
                       integer("request", "handoff_max_age_sec", 10, 600),
                       integer("request", "browser_timeout_sec", 5, 120),
                       (root / directory).resolve())


CONFIG = parse_config(load_toml("gimpo_parking"))
