from __future__ import annotations

import logging

from tg_nhl_agent.config import load_settings
from tg_nhl_agent.formatting import format_post
from tg_nhl_agent.logging_config import setup_logging
from tg_nhl_agent.redact import mask_secret

log = logging.getLogger(__name__)


def main() -> None:
    setup_logging("INFO")
    s = load_settings()

    log.info("telegram token: %s", mask_secret(s.telegram_bot_token))
    log.info("vk token: %s", mask_secret(s.vk_api_token))
    log.info("openai key: %s", mask_secret(s.openai_api_key))
    log.info("config keys: %s", list(s.config.keys()))

    text = format_post("EDM vs COL", "https://example.com/video")
    log.info("sample post:\n%s", text)


if __name__ == "__main__":
    main()
