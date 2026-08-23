from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _read_secret_file(path_value: str | None) -> str | None:
    raw = (path_value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


def env_value(name: str, default: str = "") -> str:
    file_value = _read_secret_file(os.getenv(f"{name}_FILE"))
    if file_value not in (None, ""):
        return file_value
    raw = os.getenv(name)
    if raw not in (None, ""):
        return raw
    return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_value(name, "true" if default else "false").strip().lower()
    return raw == "true"


def env_int(name: str, default: int) -> int:
    return int(env_value(name, str(default)))


def env_float(name: str, default: float) -> float:
    return float(env_value(name, str(default)))
