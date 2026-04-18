from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import requests

from tg_nhl_agent.core_scoring import build_post_public, score_and_rank
from tg_nhl_agent.favorites_loader import load_favorite_players
from tg_nhl_agent.nhl_api_results_loader import NhlApiResultsLoader

TELEGRAM_API_BASE = "https://api.telegram.org"


def build_telegram_text() -> str:
    favorite_players = load_favorite_players()
    print("favorites active:", len(favorite_players))

    now_utc = datetime.now(timezone.utc)
    window_start = now_utc - timedelta(hours=48)
    window_end = now_utc

    print("window:", window_start.isoformat(), "..", window_end.isoformat())

    loader = NhlApiResultsLoader(require_final=False)
    res = loader.load(window_start, window_end)

    print(
        "loaded:",
        "matches=",
        len(res.matches),
        "player_stats=",
        len(res.player_stats),
        "errors=",
        len(res.errors),
    )
    for e in res.errors[:5]:
        print("ERR:", e.code, e.severity.value, e.message)

    ranked = score_and_rank(res.matches, res.player_stats, favorite_players)
    post = build_post_public(
        run_date_msk=date.today(),
        ranked=ranked,
        generated_at_utc=now_utc,
    )

    header = f"NHL best 2 days — {now_utc.date().isoformat()}"

    if not post.items:
        return f"{header}\n\nNo matches found in the last 48 hours."

    lines = [header, ""]
    for idx, item in enumerate(post.items, start=1):
        match_date = item.start_time_utc.date().isoformat()
        lines.append(f"{idx:02d}. {item.title} | {match_date} | score={item.rank_score:.2f}")

    return "\n".join(lines)


def send_telegram_message(text: str) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    response = requests.post(
        f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    text = build_telegram_text()
    print("\nTELEGRAM MESSAGE:\n")
    print(text)
    result = send_telegram_message(text)
    print("\ntelegram ok:", result.get("ok"))


if __name__ == "__main__":
    main()
