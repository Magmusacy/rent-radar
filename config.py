from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

CONFIG_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CONFIG_DIR / "config.json"
EXAMPLE_PATH = CONFIG_DIR / "config.example.json"


@dataclass
class ScheduleConfig:
    hour: int = 9
    minute: int = 0
    mode: str = "arrival"


@dataclass
class ScoringConfig:
    minutes_divisor: float = 5.0
    transfer_penalty: float = 0.4
    max_commute_min: int = 40
    max_walk_min: int = 20
    min_condition: int = 4


@dataclass
class ListingsConfig:
    currency: str = "PLN"
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key_env: str = "DEEPSEEK_API_KEY"
    request_delay_sec: float = 1.5
    extraction_language: str = "English"


@dataclass
class Config:
    city: str = ""
    region: str = ""
    language: str = "en"
    destinations: Dict[str, str] = field(default_factory=dict)
    transit_modes: List[str] = field(default_factory=lambda: ["bus", "tram"])
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    listings: ListingsConfig = field(default_factory=ListingsConfig)

    def ensure_city(self, address: str) -> str:
        if self.city and self.city.lower() not in address.lower():
            return f"{address}, {self.city}"
        return address

    @property
    def transit_mode_param(self) -> str:
        return "|".join(self.transit_modes)


def _merge(default, override):
    if not isinstance(override, dict):
        return override
    for key, value in override.items():
        if not hasattr(default, key):
            continue
        current = getattr(default, key)
        if hasattr(current, "__dataclass_fields__"):
            _merge(current, value)
        else:
            setattr(default, key, value)
    return default


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    nested = {
        "schedule": ScheduleConfig,
        "scoring": ScoringConfig,
        "listings": ListingsConfig,
    }

    source = path or (CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_PATH)
    if source.exists():
        raw = json.loads(source.read_text(encoding="utf-8"))
        for key, value in raw.items():
            if not hasattr(cfg, key):
                continue
            if key in nested and isinstance(value, dict):
                _merge(getattr(cfg, key), value)
            else:
                setattr(cfg, key, value)

    if os.environ.get("CITY"):
        cfg.city = os.environ["CITY"]
    if os.environ.get("REGION"):
        cfg.region = os.environ["REGION"]

    return cfg


CONFIG = load_config()
