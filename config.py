from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # .../tg-nhl-agent
ENV_PATH = PROJECT_ROOT / ".env"
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str | None
    telegram_channel_id: str | None
    vk_api_token: str | None
    openai_api_key: str | None
    config: dict[str, Any]


def load_settings() -> Settings:
    # 1) Загружаем .env (если файл есть)
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)

    # 2) Читаем config.yaml (если есть)
    if CONFIG_PATH.exists():
        config_data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    else:
        config_data = {}

    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_channel_id=os.getenv("TELEGRAM_CHANNEL_ID"),
        vk_api_token=os.getenv("VK_API_TOKEN"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        config=config_data,
    )
