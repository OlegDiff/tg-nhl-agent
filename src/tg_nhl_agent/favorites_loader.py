from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from tg_nhl_agent.contracts import FavoritePlayer


def load_favorite_players() -> List[FavoritePlayer]:
    """
    Loads favorites from: src/tg_nhl_agent/data/favorite_players.csv
    CSV columns expected: player_id, team_id, label, is_active
    """
    path = Path(__file__).resolve().parent / "data" / "favorite_players.csv"
    if not path.exists():
        raise FileNotFoundError(f"favorite players csv not found: {path}")

    out: List[FavoritePlayer] = []
    seen: set[tuple[int, str]] = set()

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if not r:
                continue

            pid_raw = (r.get("player_id") or "").strip()
            team_id = (r.get("team_id") or "").strip().upper()
            label = (r.get("label") or "").strip() or None

            # Skip accidental repeated header rows inside the file
            if pid_raw.lower() == "player_id" or team_id.lower() == "team_id":
                continue

            # Active flag: default is "1" if column absent, otherwise respect it
            is_active = (r.get("is_active") or "1").strip()
            if is_active.lower() in ("0", "false", "no"):
                continue

            if not pid_raw or not team_id:
                continue

            try:
                pid = int(pid_raw)
            except ValueError:
                # skip bad rows instead of crashing
                continue

            key = (pid, team_id)
            if key in seen:
                continue
            seen.add(key)

            out.append(FavoritePlayer(player_id=pid, team_id=team_id, label=label))

    return out
