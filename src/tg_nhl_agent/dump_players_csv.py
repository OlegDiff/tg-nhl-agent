from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tg_nhl_agent.nhl_api_results_loader import NhlApiResultsLoader


def project_root() -> Path:
    # .../TG-NHL-AGENT/src/tg_nhl_agent -> .../TG-NHL-AGENT
    return Path(__file__).resolve().parents[2]


def main() -> None:
    root = project_root()
    out_dir = root / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    days = 14
    out_path = out_dir / "players_all_teams_2w.csv"

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    print(f"[dump] window: {start.isoformat()} .. {now.isoformat()} ({days} days)")
    print("[dump] loading NHL data (schedule + many boxscores). " "This can take a few minutes...")

    # IMPORTANT: keep require_final=False to include all games in the window.
    # If it is too slow you can switch to True, but you asked not to.
    loader = NhlApiResultsLoader(require_final=False)
    res = loader.load(start, now)

    print(
        "[dump] loaded: "
        f"matches={len(res.matches)} "
        f"player_stats_rows={len(res.player_stats)} "
        f"errors={len(res.errors)}"
    )
    if res.errors:
        print(
            "[dump] first error:",
            res.errors[0].code,
            res.errors[0].message,
            res.errors[0].details,
        )

    # Unique players per team over the period
    # key = (team_id, player_id) -> label
    players: dict[tuple[str, int], str] = {}

    skipped_unk = 0
    for ps in res.player_stats:
        team_id = (ps.team_id or "").strip().upper()
        if not team_id or team_id == "UNK":
            skipped_unk += 1
            continue

        key = (team_id, ps.player_id)
        # keep first non-empty name
        if key not in players:
            players[key] = ps.player_name or ""
        elif not players[key] and ps.player_name:
            players[key] = ps.player_name

    rows = [
        {
            "player_id": pid,
            "team_id": team,
            "label": (players[(team, pid)] or "").strip(),
            "is_active": 0,
        }
        for (team, pid) in players.keys()
    ]
    rows.sort(key=lambda r: (r["team_id"], r["label"], r["player_id"]))

    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["player_id", "team_id", "label", "is_active"])
        w.writeheader()
        w.writerows(rows)

    unique_teams = len({r["team_id"] for r in rows})
    print("[dump] wrote:", out_path)
    print(
        "[dump] unique teams:",
        unique_teams,
        "| unique (team,player):",
        len(rows),
        "| skipped UNK rows:",
        skipped_unk,
    )


if __name__ == "__main__":
    main()
