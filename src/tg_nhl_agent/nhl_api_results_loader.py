# src/tg_nhl_agent/nhl_api_results_loader.py
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from tg_nhl_agent.contracts import (
    AppError,
    MatchForScoring,
    PlayerGameStats,
    Severity,
    SourceSystem,
    Team,
)


@dataclass(frozen=True)
class LoaderResult:
    matches: List[MatchForScoring]
    player_stats: List[PlayerGameStats]
    errors: List[AppError]


class NhlApiResultsLoader:
    """
    v1 loader:
      - schedule by date:  /v1/schedule/{YYYY-MM-DD}
      - boxscore by game:  /v1/gamecenter/{gameId}/boxscore
    """

    BASE_URL = "https://api-web.nhle.com/v1"

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 0.6,
        require_final: bool = True,
    ) -> None:
        self._s = session or requests.Session()
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._backoff = retry_backoff_seconds
        self._require_final = require_final

    def load(self, window_start_utc: datetime, window_end_utc: datetime) -> LoaderResult:
        """
        window_start_utc/window_end_utc must be timezone-aware (UTC).
        """
        ws = _ensure_utc(window_start_utc)
        we = _ensure_utc(window_end_utc)

        errors: List[AppError] = []
        matches: List[MatchForScoring] = []
        player_stats: List[PlayerGameStats] = []

        dates = _dates_overlapping_window(ws, we)

        # 1) schedules
        schedule_games: List[Dict[str, Any]] = []
        for d in dates:
            try:
                payload = self._get_json(f"{self.BASE_URL}/schedule/{d.isoformat()}")
                schedule_games.extend(_extract_games_from_schedule(payload))
            except Exception as e:
                errors.append(
                    AppError(
                        code="NHL_SCHEDULE_UNAVAILABLE",
                        source=SourceSystem.NHL,
                        severity=Severity.ERROR,
                        message="Failed to fetch or parse NHL schedule",
                        details={"date": d.isoformat(), "error": repr(e)},
                    )
                )

        # schedule/{date} can return overlapping weekly data,
        # so the same game may appear several times across neighboring dates.
        schedule_games = _dedupe_games_by_id(schedule_games)

        # 2) filter games by time window + final state
        filtered: List[Tuple[int, Dict[str, Any], datetime, Optional[str]]] = []
        for g in schedule_games:
            try:
                game_id = _get_game_id(g)
                start_time = _get_start_time_utc(g)
                if start_time is None:
                    continue
                if not (ws <= start_time < we):
                    continue

                game_state = _get_game_state(g)
                if self._require_final and not _is_final_state(game_state):
                    continue

                filtered.append((game_id, g, start_time, game_state))
            except Exception:
                continue

        # 3) map matches + boxscores
        for game_id, g, start_time, game_state in filtered:
            match = _map_match_from_schedule_game(
                game_id=game_id,
                g=g,
                start_time_utc=start_time,
                game_state=game_state,
            )
            matches.append(match)

            try:
                box = self._get_json(f"{self.BASE_URL}/gamecenter/{game_id}/boxscore")
                player_stats.extend(_extract_player_stats_from_boxscore(game_id, box, match))
            except Exception as e:
                errors.append(
                    AppError(
                        code="NHL_BOXSCORE_UNAVAILABLE",
                        source=SourceSystem.NHL,
                        severity=Severity.WARNING,  # матч есть, но без статов
                        message="Failed to fetch or parse NHL boxscore",
                        details={"match_id": str(game_id), "error": repr(e)},
                    )
                )

        return LoaderResult(matches=matches, player_stats=player_stats, errors=errors)

    def _get_json(self, url: str) -> Dict[str, Any]:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self._max_retries + 1):
            try:
                r = self._s.get(url, timeout=self._timeout, headers={"Accept": "application/json"})
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_exc = e
                if attempt < self._max_retries:
                    time.sleep(self._backoff * attempt)
                    continue
                raise last_exc from None


# =========================
# Window/date helpers
# =========================


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware (UTC)")
    return dt.astimezone(timezone.utc)


def _dates_overlapping_window(ws: datetime, we: datetime) -> List[date]:
    start_d = ws.date()
    end_minus = (we - timedelta(microseconds=1)).date()
    days = (end_minus - start_d).days
    return [start_d + timedelta(days=i) for i in range(days + 1)]


def _dedupe_games_by_id(games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique_games: Dict[int, Dict[str, Any]] = {}

    for g in games:
        try:
            game_id = _get_game_id(g)
        except Exception:
            continue

        if game_id not in unique_games:
            unique_games[game_id] = g

    return list(unique_games.values())


# =========================
# Schedule parsing
# =========================


def _extract_games_from_schedule(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    games: List[Dict[str, Any]] = []

    def looks_like_game(x: Any) -> bool:
        if not isinstance(x, dict):
            return False
        has_id = ("id" in x and isinstance(x["id"], int)) or (
            "gameId" in x and isinstance(x["gameId"], int)
        )
        has_start = "startTimeUTC" in x and isinstance(x["startTimeUTC"], str)
        return has_id and has_start

    wk = payload.get("gameWeek")
    if isinstance(wk, list):
        for w in wk:
            if isinstance(w, dict) and isinstance(w.get("games"), list):
                for g in w["games"]:
                    if looks_like_game(g):
                        games.append(g)

    if games:
        return games

    # fallback scan
    for node in _walk(payload):
        if isinstance(node, dict):
            v = node.get("games")
            if isinstance(v, list):
                for g in v:
                    if looks_like_game(g):
                        games.append(g)

    return games


def _get_game_id(game_obj: Dict[str, Any]) -> int:
    if isinstance(game_obj.get("gameId"), int):
        return int(game_obj["gameId"])
    if isinstance(game_obj.get("id"), int):
        return int(game_obj["id"])
    raise KeyError("No game id in schedule object")


def _get_start_time_utc(game_obj: Dict[str, Any]) -> Optional[datetime]:
    s = game_obj.get("startTimeUTC")
    if not isinstance(s, str):
        return None
    return _parse_utc_iso(s)


def _get_game_state(game_obj: Dict[str, Any]) -> Optional[str]:
    for k in ("gameState", "gameStatus", "gameStatusText", "state"):
        v = game_obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _is_final_state(state: Optional[str]) -> bool:
    if not state:
        return False
    s = state.upper()
    return ("OFF" in s) or ("FINAL" in s) or (s == "FINAL")


def _map_match_from_schedule_game(
    game_id: int,
    g: Dict[str, Any],
    start_time_utc: datetime,
    game_state: Optional[str],
) -> MatchForScoring:
    home = _parse_team(g.get("homeTeam"), fallback_abbr="HOME")
    away = _parse_team(g.get("awayTeam"), fallback_abbr="AWAY")

    score_home = _safe_int(_deep_get(g, ["homeTeam", "score"]))
    score_away = _safe_int(_deep_get(g, ["awayTeam", "score"]))

    went_ot = bool(g.get("wentToOvertime") or g.get("overtime") or False)
    went_so = bool(g.get("wentToShootout") or g.get("shootout") or False)

    return MatchForScoring(
        match_id=game_id,
        start_time_utc=start_time_utc,
        season=None,  # MVP: optional, can be filled later
        home=home,
        away=away,
        score_home=score_home,
        score_away=score_away,
        went_overtime=went_ot,
        went_shootout=went_so,
    )


def _parse_team(team_obj: Any, fallback_abbr: str) -> Team:
    if not isinstance(team_obj, dict):
        return Team(team_id=fallback_abbr, name=fallback_abbr, abbr=fallback_abbr)

    abbr = None
    for k in ("abbrev", "abbr", "triCode"):
        if isinstance(team_obj.get(k), str):
            abbr = team_obj[k]
            break
    if abbr is None:
        ta = team_obj.get("teamAbbrev")
        if isinstance(ta, dict) and isinstance(ta.get("default"), str):
            abbr = ta["default"]

    name = None
    nm = team_obj.get("name")
    if isinstance(nm, dict) and isinstance(nm.get("default"), str):
        name = nm["default"]
    elif isinstance(team_obj.get("commonName"), dict) and isinstance(
        team_obj["commonName"].get("default"), str
    ):
        name = team_obj["commonName"]["default"]
    elif isinstance(team_obj.get("teamName"), dict) and isinstance(
        team_obj["teamName"].get("default"), str
    ):
        name = team_obj["teamName"]["default"]

    abbr = abbr or fallback_abbr
    name = name or abbr

    # договоримся: team_id == abbr (строка)
    return Team(team_id=abbr, name=name, abbr=abbr)


# =========================
# Boxscore parsing
# =========================


def _extract_player_stats_from_boxscore(
    match_id: int,
    box: Dict[str, Any],
    match: MatchForScoring,
) -> List[PlayerGameStats]:
    out: List[PlayerGameStats] = []

    pgs = box.get("playerByGameStats")
    if isinstance(pgs, dict):
        out.extend(
            _extract_players_from_team_stats(
                match_id, pgs.get("homeTeam"), match.home.abbr or match.home.team_id
            )
        )
        out.extend(
            _extract_players_from_team_stats(
                match_id, pgs.get("awayTeam"), match.away.abbr or match.away.team_id
            )
        )
        if out:
            return out

    # fallback scan
    for node in _walk(box):
        if isinstance(node, dict) and isinstance(node.get("playerId"), int):
            goals = _safe_int(node.get("goals")) or 0
            assists = _safe_int(node.get("assists")) or 0
            out.append(
                PlayerGameStats(
                    match_id=match_id,
                    player_id=int(node["playerId"]),
                    player_name=_player_name(node),
                    team_id="UNK",
                    goals=goals,
                    assists=assists,
                )
            )
    return out


def _extract_players_from_team_stats(
    match_id: int, team_stats_obj: Any, team_id: str
) -> List[PlayerGameStats]:
    if not isinstance(team_stats_obj, dict):
        return []

    player_lists: List[List[Dict[str, Any]]] = []
    for k in ("forwards", "defense", "defence", "goalies", "skaters", "players"):
        v = team_stats_obj.get(k)
        if isinstance(v, list):
            player_lists.append([p for p in v if isinstance(p, dict)])

    if not player_lists:
        for node in _walk(team_stats_obj):
            if isinstance(node, dict):
                for k in ("forwards", "defense", "goalies", "skaters"):
                    v = node.get(k)
                    if isinstance(v, list):
                        player_lists.append([p for p in v if isinstance(p, dict)])

    out: List[PlayerGameStats] = []
    for lst in player_lists:
        for p in lst:
            if not (isinstance(p.get("playerId"), int)):
                continue
            out.append(
                PlayerGameStats(
                    match_id=match_id,
                    player_id=int(p["playerId"]),
                    player_name=_player_name(p),
                    team_id=team_id,
                    goals=_safe_int(p.get("goals")) or 0,
                    assists=_safe_int(p.get("assists")) or 0,
                )
            )
    return out


def _player_name(p: Dict[str, Any]) -> str:
    nm = p.get("name")
    if isinstance(nm, dict) and isinstance(nm.get("default"), str):
        return nm["default"]
    if isinstance(nm, str):
        return nm

    fn = p.get("firstName")
    ln = p.get("lastName")
    if isinstance(fn, dict):
        fn = fn.get("default")
    if isinstance(ln, dict):
        ln = ln.get("default")
    if isinstance(fn, str) and isinstance(ln, str):
        return f"{fn} {ln}"
    return "Unknown"


# =========================
# Generic JSON helpers
# =========================


def _parse_utc_iso(s: str) -> datetime:
    ss = s.strip()
    if ss.endswith("Z"):
        ss = ss[:-1] + "+00:00"
    dt = datetime.fromisoformat(ss)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_int(x: Any) -> Optional[int]:
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x)
    if isinstance(x, str):
        try:
            return int(x)
        except ValueError:
            return None
    return None


def _deep_get(obj: Any, path: List[str]) -> Any:
    cur = obj
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _walk(node: Any) -> Iterable[Any]:
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, dict):
            for v in cur.values():
                stack.append(v)
        elif isinstance(cur, list):
            for v in cur:
                stack.append(v)


# =========================
# Manual smoke run
# =========================
if __name__ == "__main__":
    loader = NhlApiResultsLoader(require_final=False)
    now = datetime.now(timezone.utc)
    res = loader.load(now - timedelta(hours=48), now)

    print("matches:", len(res.matches))
    print("player_stats:", len(res.player_stats))
    print("errors:", len(res.errors))
    if res.errors:
        print(res.errors[0])
    if res.matches:
        m0 = res.matches[0]
        print("sample match:", m0.away.abbr, "@", m0.home.abbr, m0.start_time_utc.isoformat())
