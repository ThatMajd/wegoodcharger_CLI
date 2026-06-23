from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import DeviceRef


APP_DIR = "wegoodcharger-cli"
CONFIG_FILE = "config.json"


@dataclass
class Config:
    email: str | None = None
    base_url: str = "https://ev.weguyun.com"
    default_device: DeviceRef | None = None


def config_dir() -> Path:
    root = os.environ.get("WEGOODCHARGER_CONFIG_DIR")
    if root:
        return Path(root)

    home = Path.home()
    return home / ".config" / APP_DIR


def config_path() -> Path:
    return config_dir() / CONFIG_FILE


def load_config() -> Config:
    path = config_path()
    if not path.exists():
        return Config()

    data = json.loads(path.read_text(encoding="utf-8"))
    default_device = None
    if isinstance(data.get("default_device"), dict):
        default_device = DeviceRef.from_config(data["default_device"])

    return Config(
        email=data.get("email"),
        base_url=data.get("base_url") or "https://ev.weguyun.com",
        default_device=default_device,
    )


def save_config(config: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "email": config.email,
        "base_url": config.base_url,
        "default_device": config.default_device.to_config() if config.default_device else None,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
