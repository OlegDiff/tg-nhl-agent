# src/tg_nhl_agent/usecase.py
"""
MVP v1: NHL rewatch recommender WITHOUT VK links.

Содержит:
- Порты (Protocol) для адаптеров:
  - results_loader (матчи + статы игроков)
  - rules_loader (таблица весов/интереса)
  - publisher (публикация текста)
  - registry (идемпотентность, но отключается в DRY_RUN)
- Оркестрацию пайплайна: load -> score -> rank -> post -> publish
- Рендер в текст (без спойлеров, без ссылок на видео)
- Мок-адаптеры (smoke test)

DRY_RUN:
- если cfg.dry_run = True, то:
  - НЕ выполняем идемпотентность (не блокируем повторные запуски)
  - НЕ записываем state (registry.set_published не вызывается)
  - publish всё равно вызывается (в dev это StdoutPublisher)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Protocol, Tuple
from zoneinfo import ZoneInfo

from .contracts import (
    AppError,
    MatchForScoring,
    MatchScore,
    PlayerGameStats,
    PostItemPublic,
    PostPublic,
    PublicationRecord,
    RankedMatch,
    ScoreContribution,
    ScoringRule,
    Severity,
    SourceSystem,
    Team,
)

# -----------------------------
# Config
# -----------------------------


@dataclass(frozen=True)
class UsecaseConfig:
    tz_msk: str = "Europe/Moscow"
    publish_hour_msk: int = 8
    lookback_hours: int = 48
    min_interest_score: float = 0.0

    # DEV режим: не блокируем повторные запуски, не пишем state
    dry_run: bool = False


# -----------------------------
# Ports (adapters interfaces)
# -----------------------------


class ResultsLoader(Protocol):
    def load(
        self,
        window_start_utc: datetime,
        window_end_utc: datetime,
    ) -> Tuple[List[MatchForScoring], List[PlayerGameStats], List[AppError]]: ...


class ScoringRulesLoader(Protocol):
    def load(self) -> Tuple[List[ScoringRule], List[AppError]]: ...


class Publisher(Protocol):
    def publish(self, text: str) -> Tuple[Optional[str], List[AppError]]: ...


class PublicationRegistry(Protocol):
    def get(self, run_date_msk: date) -> Tuple[Optional[PublicationRecord], List[AppError]]: ...

    def set_published(self, record: PublicationRecord) -> List[AppError]: ...


# -----------------------------
# Helpers: time window
# -----------------------------


def compute_window(now_utc: datetime, cfg: UsecaseConfig) -> Tuple[date, datetime, datetime]:
    """
    Окно: последние cfg.lookback_hours часов от 08:00 MSK текущего дня.
    Если запустились раньше 08:00 MSK — используем якорь предыдущего дня.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    tz_msk = ZoneInfo(cfg.tz_msk)
    now_msk = now_utc.astimezone(tz_msk)

    anchor_msk = now_msk.replace(hour=cfg.publish_hour_msk, minute=0, second=0, microsecond=0)
    if now_msk < anchor_msk:
        anchor_msk -= timedelta(days=1)

    run_date_msk = anchor_msk.date()
    window_end_msk = anchor_msk
    window_start_msk = anchor_msk - timedelta(hours=cfg.lookback_hours)

    return (
        run_date_msk,
        window_start_msk.astimezone(timezone.utc),
        window_end_msk.astimezone(timezone.utc),
    )


# -----------------------------
# Core: scoring & ranking
# -----------------------------


def _index_rules(rules: List[ScoringRule]) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Собираем веса по игрокам и командам."""
    player_w: Dict[str, float] = {}
    team_w: Dict[str, float] = {}

    for r in rules:
        et = (r.entity_type or "").strip().lower()
        if et == "player":
            player_w[r.entity_id] = player_w.get(r.entity_id, 0.0) + float(r.weight)
        elif et == "team":
            team_w[r.entity_id] = team_w.get(r.entity_id, 0.0) + float(r.weight)

    return player_w, team_w


def score_matches(
    matches: List[MatchForScoring],
    player_stats: List[PlayerGameStats],
    rules: List[ScoringRule],
) -> List[RankedMatch]:
    """
    Черновая формула интересности:
    - + вес команды (home + away), если есть в rules
    - + вес игрока * (goals + assists*0.5), если есть в rules
    - + бонусы OT/SO
    - + небольшой бонус за total_goals (только внутренняя логика; наружу не выводим)
    """
    player_w, team_w = _index_rules(rules)

    stats_by_match: Dict[str, List[PlayerGameStats]] = {}
    for ps in player_stats:
        stats_by_match.setdefault(ps.match_id, []).append(ps)

    ranked: List[RankedMatch] = []
    for m in matches:
        score = 0.0
        contribs: List[ScoreContribution] = []

        for t in (m.home, m.away):
            w = team_w.get(t.team_id)
            if w:
                score += w
                contribs.append(
                    ScoreContribution(
                        reason="team_weight",
                        weight=w,
                        entity_type="team",
                        entity_id=t.team_id,
                    )
                )

        for ps in stats_by_match.get(m.match_id, []):
            w = player_w.get(ps.player_id)
            if not w:
                continue
            factor = float(ps.goals) + 0.5 * float(ps.assists)
            delta = w * factor
            if delta:
                score += delta
                contribs.append(
                    ScoreContribution(
                        reason="player_weight",
                        weight=delta,
                        entity_type="player",
                        entity_id=ps.player_id,
                    )
                )

        if m.went_overtime:
            score += 0.2
            contribs.append(ScoreContribution(reason="overtime_bonus", weight=0.2))
        if m.went_shootout:
            score += 0.2
            contribs.append(ScoreContribution(reason="shootout_bonus", weight=0.2))

        total_goals = int(m.score_home) + int(m.score_away)
        tg_bonus = min(1.0, total_goals / 10.0) * 0.2
        if tg_bonus:
            score += tg_bonus
            contribs.append(ScoreContribution(reason="total_goals_bonus", weight=tg_bonus))

        ranked.append(
            RankedMatch(
                match=m,
                rank=MatchScore(match_id=m.match_id, score=score, contributions=contribs),
            )
        )

    ranked.sort(key=lambda rm: rm.rank.score, reverse=True)
    return ranked


# -----------------------------
# Post building (public)
# -----------------------------


def _match_title(m: MatchForScoring) -> str:
    h = m.home.abbr or m.home.name
    a = m.away.abbr or m.away.name
    return f"{h} — {a}"


def build_post_public(
    run_date_msk: date,
    now_utc: datetime,
    ranked: List[RankedMatch],
    cfg: UsecaseConfig,
    extra_errors: List[AppError],
) -> PostPublic:
    """
    MVP без VK:
    - items: только матч (title) + rank_score (для дебага)
    - никаких ссылок
    """
    items: List[PostItemPublic] = []
    errors: List[AppError] = list(extra_errors)

    for rm in ranked:
        if rm.rank.score < cfg.min_interest_score:
            continue

        items.append(
            PostItemPublic(
                match_id=rm.match.match_id,
                title=_match_title(rm.match),
                start_time_utc=rm.match.start_time_utc,
                highlights_url=None,  # не используем в MVP
                full_url=None,  # не используем в MVP
                rank_score=rm.rank.score,
            )
        )

    return PostPublic(
        run_date_msk=run_date_msk,
        generated_at_utc=now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=timezone.utc),
        items=items,
        errors=errors,
    )


# -----------------------------
# Render (public text)
# -----------------------------


def render_post(post: PostPublic) -> str:
    """
    MVP без VK: выводим только список матчей.
    """
    lines: List[str] = []

    if not post.items:
        lines.append("Матчей нет.")
    else:
        for it in post.items:
            lines.append(it.title)
        # пустая строка после списка (аккуратно)
        lines.append("")

    if post.errors:
        lines.append("⚠️ Проблемы:")
        seen = set()
        for e in post.errors:
            key = (e.code, e.source.value, e.message)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- [{e.source.value}] {e.code}: {e.message}")

    return "\n".join(lines).strip()


# -----------------------------
# Use-case orchestration
# -----------------------------


def run_daily_digest(
    now_utc: datetime,
    cfg: UsecaseConfig,
    results_loader: ResultsLoader,
    rules_loader: ScoringRulesLoader,
    publisher: Publisher,
    registry: PublicationRegistry,
) -> Tuple[Optional[PostPublic], List[AppError]]:
    errors: List[AppError] = []

    run_date_msk, window_start_utc, window_end_utc = compute_window(now_utc, cfg)

    # Idempotency: только в PROD
    if not cfg.dry_run:
        rec, reg_err = registry.get(run_date_msk)
        errors.extend(reg_err)
        if rec and rec.published:
            errors.append(
                AppError(
                    code="ALREADY_PUBLISHED",
                    source=SourceSystem.CORE,
                    severity=Severity.WARNING,
                    message=(
                        f"Пост за {run_date_msk} уже был опубликован "
                        f"(tg_message_id={rec.tg_message_id})."
                    ),
                )
            )
            return None, errors
    else:
        errors.append(
            AppError(
                code="DRY_RUN",
                source=SourceSystem.CORE,
                severity=Severity.WARNING,
                message="DRY_RUN=true: идемпотентность отключена, state не будет записан.",
            )
        )

    # load results
    matches, player_stats, res_err = results_loader.load(window_start_utc, window_end_utc)
    errors.extend(res_err)

    # если матчей нет — публикуем "Матчей нет."
    if not matches:
        post = PostPublic(
            run_date_msk=run_date_msk, generated_at_utc=now_utc, items=[], errors=errors
        )
        text = render_post(post)
        msg_id, pub_err = publisher.publish(text)
        errors.extend(pub_err)

        if (not cfg.dry_run) and msg_id:
            errors.extend(
                registry.set_published(
                    PublicationRecord(
                        run_date_msk=run_date_msk,
                        published=True,
                        tg_message_id=msg_id,
                        published_at_utc=now_utc
                        if now_utc.tzinfo
                        else now_utc.replace(tzinfo=timezone.utc),
                    )
                )
            )

        return post, errors

    # load rules
    rules, rules_err = rules_loader.load()
    errors.extend(rules_err)

    # score + rank
    ranked = score_matches(matches, player_stats, rules)

    # build post (без видео)
    post = build_post_public(run_date_msk, now_utc, ranked, cfg, extra_errors=errors)

    # render & publish
    text = render_post(post)
    msg_id, pub_err = publisher.publish(text)
    errors.extend(pub_err)

    # record publication (только PROD)
    if (not cfg.dry_run) and msg_id:
        errors.extend(
            registry.set_published(
                PublicationRecord(
                    run_date_msk=run_date_msk,
                    published=True,
                    tg_message_id=msg_id,
                    published_at_utc=now_utc
                    if now_utc.tzinfo
                    else now_utc.replace(tzinfo=timezone.utc),
                )
            )
        )

    # финализируем ошибки в post
    post = PostPublic(
        run_date_msk=post.run_date_msk,
        generated_at_utc=post.generated_at_utc,
        items=post.items,
        errors=errors,
    )
    return post, errors


# -----------------------------
# Minimal mocks (for smoke test)
# -----------------------------


class MockResultsLoader:
    def load(self, window_start_utc: datetime, window_end_utc: datetime):
        m1 = MatchForScoring(
            match_id="m1",
            start_time_utc=window_end_utc - timedelta(hours=6),
            season="2025-2026",
            home=Team(team_id="BOS", name="Boston Bruins", abbr="BOS"),
            away=Team(team_id="NYR", name="New York Rangers", abbr="NYR"),
            score_home=4,
            score_away=3,
            went_overtime=True,
            went_shootout=False,
        )
        m2 = MatchForScoring(
            match_id="m2",
            start_time_utc=window_end_utc - timedelta(hours=20),
            season="2025-2026",
            home=Team(team_id="TOR", name="Toronto Maple Leafs", abbr="TOR"),
            away=Team(team_id="MTL", name="Montreal Canadiens", abbr="MTL"),
            score_home=2,
            score_away=1,
            went_overtime=False,
            went_shootout=False,
        )

        stats = [
            PlayerGameStats(
                match_id="m1",
                player_id="p_1",
                player_name="Star Player",
                team_id="BOS",
                goals=2,
                assists=1,
            ),
            PlayerGameStats(
                match_id="m2",
                player_id="p_2",
                player_name="Other Player",
                team_id="TOR",
                goals=1,
                assists=0,
            ),
        ]
        return [m1, m2], stats, []


class MockScoringRulesLoader:
    def load(self):
        rules = [
            ScoringRule(entity_type="team", entity_id="BOS", weight=1.5, label="favorite team"),
            ScoringRule(entity_type="player", entity_id="p_1", weight=2.0, label="favorite player"),
        ]
        return rules, []


class StdoutPublisher:
    def publish(self, text: str):
        print("----- PUBLISH -----")
        print(text)
        print("-------------------")
        return "mock_message_id", []


class NoopRegistry:
    def get(self, run_date_msk: date):
        return None, []

    def set_published(self, record: PublicationRecord):
        return []


if __name__ == "__main__":
    cfg = UsecaseConfig(min_interest_score=0.0, dry_run=True)  # DEV
    now_utc = datetime.now(timezone.utc)

    post, errs = run_daily_digest(
        now_utc=now_utc,
        cfg=cfg,
        results_loader=MockResultsLoader(),
        rules_loader=MockScoringRulesLoader(),
        publisher=StdoutPublisher(),
        registry=NoopRegistry(),
    )

    print("errors:", errs)
