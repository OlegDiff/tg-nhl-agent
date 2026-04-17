# src/tg_nhl_agent/core_scoring.py
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import DefaultDict, Dict, List

from tg_nhl_agent.contracts import (
    FavoritePlayer,
    MatchForScoring,
    MatchScore,
    PlayerGameStats,
    PostItemPublic,
    PostPublic,
    RankedMatch,
    ScoreContribution,
)

# Weights (v1)
GOAL_WEIGHT = 1.0
ASSIST_WEIGHT = 0.3
HATTRICK_BONUS = 5.0  # goals >= 3
THREE_ASSISTS_BONUS = 1.0  # assists >= 3
OVERTIME_BONUS = 1.0


def score_and_rank(
    matches: List[MatchForScoring],
    player_stats: List[PlayerGameStats],
    favorite_players: List[FavoritePlayer],
) -> List[RankedMatch]:
    """
    Формула v1:

    score =
      total_goals (если известны оба счета)
      + 1.0 если OT
      + для каждого игрока из списка (FavoritePlayer):
          + 1.0 * goals
          + 0.3 * assists
          + 5.0 если hattrick (goals >= 3)
          + 1.0 если 3 assists (assists >= 3)

    Оптимизация:
      - favorite_players индексируются по team_id -> set[player_id]
      - при обходе статов матча мы сразу отбрасываем игроков не из нужных команд
    """

    # team_id -> set(player_id)
    fav_by_team: Dict[str, set[int]] = {}
    for fp in favorite_players:
        fav_by_team.setdefault(fp.team_id, set()).add(fp.player_id)

    # match_id -> player_id -> aggregated PlayerGameStats
    # (на случай если в источнике есть дубль строк на игрока: суммируем)
    by_match_player: DefaultDict[int, Dict[int, PlayerGameStats]] = defaultdict(dict)
    for ps in player_stats:
        cur = by_match_player[ps.match_id].get(ps.player_id)
        if cur is None:
            by_match_player[ps.match_id][ps.player_id] = ps
        else:
            by_match_player[ps.match_id][ps.player_id] = PlayerGameStats(
                match_id=ps.match_id,
                player_id=ps.player_id,
                player_name=ps.player_name or cur.player_name,
                team_id=ps.team_id or cur.team_id,
                goals=(cur.goals + ps.goals),
                assists=(cur.assists + ps.assists),
            )

    ranked: List[RankedMatch] = []

    for m in matches:
        score = 0.0
        contrib: List[ScoreContribution] = []

        # 1) total goals (spoiler used only inside scoring)
        if m.score_home is not None and m.score_away is not None:
            tg = int(m.score_home) + int(m.score_away)
            score += float(tg)
            contrib.append(ScoreContribution(reason="total_goals", weight=float(tg)))

        # 2) OT bonus
        if m.went_overtime:
            score += OVERTIME_BONUS
            contrib.append(ScoreContribution(reason="overtime_bonus", weight=OVERTIME_BONUS))

        # 3) Favorite players bonuses (filtered by team)
        mp = by_match_player.get(m.match_id, {})
        if mp and fav_by_team:
            for pid, ps in mp.items():
                # fast reject: team not tracked or player not in that team's set
                team_set = fav_by_team.get(ps.team_id)
                if not team_set or pid not in team_set:
                    continue

                if ps.goals:
                    w = GOAL_WEIGHT * float(ps.goals)
                    score += w
                    contrib.append(
                        ScoreContribution(
                            reason="fav_player_goals",
                            weight=w,
                            entity_type="player",
                            entity_id=str(pid),
                        )
                    )

                if ps.assists:
                    w = ASSIST_WEIGHT * float(ps.assists)
                    score += w
                    contrib.append(
                        ScoreContribution(
                            reason="fav_player_assists",
                            weight=w,
                            entity_type="player",
                            entity_id=str(pid),
                        )
                    )

                if ps.goals >= 3:
                    score += HATTRICK_BONUS
                    contrib.append(
                        ScoreContribution(
                            reason="fav_player_hattrick_bonus",
                            weight=HATTRICK_BONUS,
                            entity_type="player",
                            entity_id=str(pid),
                        )
                    )

                if ps.assists >= 3:
                    score += THREE_ASSISTS_BONUS
                    contrib.append(
                        ScoreContribution(
                            reason="fav_player_3_assists_bonus",
                            weight=THREE_ASSISTS_BONUS,
                            entity_type="player",
                            entity_id=str(pid),
                        )
                    )

        ms = MatchScore(match_id=m.match_id, score=score, contributions=contrib)
        ranked.append(RankedMatch(match=m, rank=ms))

    # Sort by score desc, then by start_time desc
    ranked.sort(key=lambda rm: (rm.rank.score, rm.match.start_time_utc), reverse=True)
    return ranked


def build_post_public(
    run_date_msk: date,
    ranked: List[RankedMatch],
    generated_at_utc: datetime,
) -> PostPublic:
    """
    Build spoiler-free post (no scores, no stats, no OT flags).
    """
    items: List[PostItemPublic] = []
    for rm in ranked:
        m = rm.match
        title = f"{m.away.abbr or m.away.team_id} — {m.home.abbr or m.home.team_id}"
        items.append(
            PostItemPublic(
                match_id=m.match_id,
                title=title,
                start_time_utc=m.start_time_utc,
                highlights_url=None,
                full_url=None,
                rank_score=rm.rank.score,  # можно не печатать в тексте
            )
        )

    return PostPublic(
        run_date_msk=run_date_msk,
        generated_at_utc=generated_at_utc,
        items=items,
        errors=[],
    )
