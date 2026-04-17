# src/tg_nhl_agent/smoke_core.py
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from tg_nhl_agent.core_scoring import build_post_public, score_and_rank
from tg_nhl_agent.favorites_loader import load_favorite_players
from tg_nhl_agent.nhl_api_results_loader import NhlApiResultsLoader


def main() -> None:
    fav = load_favorite_players()
    print("favorites active:", len(fav))

    loader = NhlApiResultsLoader(require_final=False)
    now = datetime.now(timezone.utc)
    res = loader.load(now - timedelta(hours=48), now)

    print(
        "loaded:",
        "matches=",
        len(res.matches),
        "player_stats=",
        len(res.player_stats),
        "errors=",
        len(res.errors),
    )
    if res.errors:
        for e in res.errors[:10]:
            print("ERR:", e.code, e.severity.value, e.message, e.details)

    ranked = score_and_rank(res.matches, res.player_stats, fav)
    post = build_post_public(run_date_msk=date.today(), ranked=ranked, generated_at_utc=now)

    print("\nranked:", len(post.items))
    for it in post.items:
        print(it.title, it.start_time_utc.isoformat(), "score=", it.rank_score)


if __name__ == "__main__":
    main()
