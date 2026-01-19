# usecase.py
"""
Walking skeleton use-case for the NHL rewatch recommender.

Содержит:
- Порты (Protocol) для адаптеров
- Оркестрацию пайплайна: load -> score -> rank -> video -> post -> publish
- Минимальный рендер в текст (без спойлеров)
- Мок-адаптеры (чтобы можно было сразу запустить и увидеть результат)

Важно:
- usecase НЕ тянет сеть/БД сам. Всё внешнее — через порты.
- Данные для расчета могут содержать счет/статы, но публичный пост их не содержит.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Protocol, Tuple
from zoneinfo import ZoneInfo

from .contracts import (
    # data
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
    # enums
    Severity,
    SourceSystem,
    Team,
    VideoKind,
    VideoLinkResult,
    VideoStatus,
)

# -----------------------------
# Config
# -----------------------------


@dataclass(frozen=True)
class UsecaseConfig:
    tz_msk: str = "Europe/Moscow"
    publish_hour_msk: int = 8

    # window = последние 48 часов от 08:00 MSK текущего дня
    lookback_hours: int = 48

    # фильтр по интересности (можно настроить позже)
    min_interest_score: float = 0.0


# -----------------------------
# Ports (adapters interfaces)
# -----------------------------


class ResultsLoader(Protocol):
    def load(
        self,
        window_start_utc: datetime,
        window_end_utc: datetime,
    ) -> Tuple[List[MatchForScoring], List[PlayerGameStats], List[AppError]]:
        """
        Должен вернуть завершенные матчи за окно времени + агрегированные статы игроков.
        Ошибки кладем в errors (не исключениями), если это не фатально.
        """
        ...


class ScoringRulesLoader(Protocol):
    def load(self) -> Tuple[List[ScoringRule], List[AppError]]:
        """Возвращает таблицу 'повышающих очков'."""
        ...


class VideoLoader(Protocol):
    def load_for_matches(
        self,
        match_ids: List[str],
    ) -> Tuple[List[VideoLinkResult], List[AppError]]:
        """
        Возвращает результаты поиска видео по всем матчам и видам роликов.
        Рекомендуется возвращать ровно 2 результата на матч (highlights + full),
        со статусами FOUND/NOT_FOUND/ERROR.
        """
        ...


class Publisher(Protocol):
    def publish(self, text: str) -> Tuple[Optional[str], List[AppError]]:
        """
        Публикует текст в Telegram (или куда угодно).
        Возвращает message_id (если применимо) и errors.
        """
        ...


class PublicationRegistry(Protocol):
    def get(self, run_date_msk: date) -> Tuple[Optional[PublicationRecord], List[AppError]]:
        """Читает запись об уже выполненной публикации (идемпотентность)."""
        ...

    def set_published(self, record: PublicationRecord) -> List[AppError]:
        """Сохраняет факт публикации."""
        ...


# -----------------------------
# Helpers: time window
# -----------------------------


def compute_window(now_utc: datetime, cfg: UsecaseConfig) -> Tuple[date, datetime, datetime]:
    """
    Определяем "день запуска" по Москве и окно последних 48 часов,
    считая от 08:00 MSK этого дня.

    Возвращает:
    - run_date_msk (ключ дня)
    - window_start_utc
    - window_end_utc
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    tz_msk = ZoneInfo(cfg.tz_msk)
    now_msk = now_utc.astimezone(tz_msk)

    # привязка к 08:00 текущего московского дня
    anchor_msk = now_msk.replace(hour=cfg.publish_hour_msk, minute=0, second=0, microsecond=0)

    # если мы запустились раньше 08:00 (например тест), якорь — предыдущий день 08:00
    if now_msk < anchor_msk:
        anchor_msk = anchor_msk - timedelta(days=1)

    run_date_msk = anchor_msk.date()

    window_end_msk = anchor_msk
    window_start_msk = anchor_msk - timedelta(hours=cfg.lookback_hours)

    window_start_utc = window_start_msk.astimezone(timezone.utc)
    window_end_utc = window_end_msk.astimezone(timezone.utc)

    return run_date_msk, window_start_utc, window_end_utc


# -----------------------------
# Core: scoring & ranking
# -----------------------------


def _index_rules(rules: List[ScoringRule]) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Разделяем rules на веса по player_id и team_id."""
    player_w: Dict[str, float] = {}
    team_w: Dict[str, float] = {}

    for r in rules:
        et = (r.entity_type or "").strip().lower()
        if et == "player":
            player_w[r.entity_id] = player_w.get(r.entity_id, 0.0) + float(r.weight)
        elif et == "team":
            team_w[r.entity_id] = team_w.get(r.entity_id, 0.0) + float(r.weight)
        else:
            # неизвестный entity_type — игнорируем; это лучше ловить валидатором
            continue
    return player_w, team_w


def score_matches(
    matches: List[MatchForScoring],
    player_stats: List[PlayerGameStats],
    rules: List[ScoringRule],
) -> List[RankedMatch]:
    """
    Черновая формула интересности (можно менять):
    - базовый интерес = 0
    - + вес команды (home + away), если есть в rules
    - + вес игрока * (goals + assists*0.5), если есть в rules
    - + небольшой бонус за OT/SO (если известен)
    - + небольшой бонус за общий тотал шайб (score_home + score_away) (без вывода наружу)
    """
    player_w, team_w = _index_rules(rules)

    stats_by_match: Dict[str, List[PlayerGameStats]] = {}
    for ps in player_stats:
        stats_by_match.setdefault(ps.match_id, []).append(ps)

    ranked: List[RankedMatch] = []
    for m in matches:
        score = 0.0
        contribs: List[ScoreContribution] = []

        # team weights
        for t in (m.home, m.away):
            w = team_w.get(t.team_id)
            if w:
                score += w
                contribs.append(
                    ScoreContribution(
                        reason="team_weight", weight=w, entity_type="team", entity_id=t.team_id
                    )
                )

        # player weights
        for ps in stats_by_match.get(m.match_id, []):
            w = player_w.get(ps.player_id)
            if not w:
                continue
            factor = float(ps.goals) + 0.5 * float(ps.assists)
            delta = w * factor
            if delta != 0:
                score += delta
                contribs.append(
                    ScoreContribution(
                        reason="player_weight",
                        weight=delta,
                        entity_type="player",
                        entity_id=ps.player_id,
                    )
                )

        # overtime / shootout
        if m.went_overtime:
            score += 0.2
            contribs.append(ScoreContribution(reason="overtime_bonus", weight=0.2))
        if m.went_shootout:
            score += 0.2
            contribs.append(ScoreContribution(reason="shootout_bonus", weight=0.2))

        # total goals bonus (спойлерно, но только для расчета)
        total_goals = int(m.score_home) + int(m.score_away)
        tg_bonus = min(1.0, total_goals / 10.0) * 0.2  # мягкий бонус
        if tg_bonus:
            score += tg_bonus
            contribs.append(ScoreContribution(reason="total_goals_bonus", weight=tg_bonus))

        ranked.append(
            RankedMatch(
                match=m, rank=MatchScore(match_id=m.match_id, score=score, contributions=contribs)
            )
        )

    # ранжирование: от большего score к меньшему
    ranked.sort(key=lambda rm: rm.rank.score, reverse=True)
    return ranked


# -----------------------------
# Core: videos -> public post items
# -----------------------------


def _match_title(m: MatchForScoring) -> str:
    """Короткий титул без спойлеров: ABBR — ABBR или name vs name."""
    h = m.home.abbr or m.home.name
    a = m.away.abbr or m.away.name
    return f"{h} — {a}"


def build_post_public(
    run_date_msk: date,
    now_utc: datetime,
    ranked: List[RankedMatch],
    video_results: List[VideoLinkResult],
    cfg: UsecaseConfig,
    extra_errors: List[AppError],
) -> PostPublic:
    """
    Собираем PostPublic (без спойлеров).
    - матчи фильтруем по порогу интересности
    - ссылки подставляем по kind
    - если ссылки нет — оставляем None (рендер покажет 'не найдена')
    """
    # индекс видео: match_id -> {kind -> VideoLinkResult}
    vmap: Dict[str, Dict[VideoKind, VideoLinkResult]] = {}
    for vr in video_results:
        vmap.setdefault(vr.match_id, {})[vr.kind] = vr

    items: List[PostItemPublic] = []
    errors: List[AppError] = list(extra_errors)

    for rm in ranked:
        if rm.rank.score < cfg.min_interest_score:
            continue

        vids = vmap.get(rm.match.match_id, {})
        h = vids.get(VideoKind.HIGHLIGHTS)
        f = vids.get(VideoKind.FULL)

        highlights_url = h.url if (h and h.status == VideoStatus.FOUND) else None
        full_url = f.url if (f and f.status == VideoStatus.FOUND) else None

        # если ERROR — добавляем ошибку в общий список (как warning)
        for vr in (h, f):
            if vr and vr.status == VideoStatus.ERROR and vr.error:
                errors.append(vr.error)

        items.append(
            PostItemPublic(
                match_id=rm.match.match_id,
                title=_match_title(rm.match),
                start_time_utc=rm.match.start_time_utc,
                highlights_url=highlights_url,
                full_url=full_url,
                rank_score=rm.rank.score,  # не обязаны выводить, но держим для дебага
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
    Рендерит PostPublic в текст для Telegram.
    Без счетов и без деталей событий.
    """
    lines: List[str] = []

    if not post.items:
        lines.append("Матчей нет.")
    else:
        for it in post.items:
            lines.append(it.title)

            # highlights
            if it.highlights_url:
                lines.append(f"  highlights: {it.highlights_url}")
            else:
                lines.append("  highlights: ссылка не найдена")

            # full
            if it.full_url:
                lines.append(f"  full: {it.full_url}")
            else:
                lines.append("  full: ссылка не найдена")

            lines.append("")  # пустая строка между матчами

    # ошибки/предупреждения (внизу)
    if post.errors:
        lines.append("⚠️ Проблемы:")
        # убираем дубли по (code, source, message)
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
    video_loader: VideoLoader,
    publisher: Publisher,
    registry: PublicationRegistry,
) -> Tuple[Optional[PostPublic], List[AppError]]:
    """
    Главный daily use-case:
    1) time window
    2) идемпотентность
    3) load results
    4) load rules
    5) score+rank
    6) load videos
    7) build post + render
    8) publish + register

    Возвращает:
    - post (если дошли до построения)
    - errors (сквозные)
    """
    errors: List[AppError] = []

    run_date_msk, window_start_utc, window_end_utc = compute_window(now_utc, cfg)

    # idempotency check
    rec, reg_err = registry.get(run_date_msk)
    errors.extend(reg_err)

    if rec and rec.published:
        # Уже публиковали — выходим без дубля
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

    # load results
    matches, player_stats, res_err = results_loader.load(window_start_utc, window_end_utc)
    errors.extend(res_err)

    # если вообще нет матчей — формируем пост "Матчей нет" (это не ошибка)
    # но если при этом есть фатальная ошибка NHL — ты можешь потом поменять правило.
    # Сейчас придерживаемся UX: если матчей нет — пишем "Матчей нет."
    # А ошибки (если есть) добавятся внизу.
    if not matches:
        post = PostPublic(
            run_date_msk=run_date_msk, generated_at_utc=now_utc, items=[], errors=errors
        )
        text = render_post(post)
        msg_id, pub_err = publisher.publish(text)
        errors.extend(pub_err)
        errors.extend(
            registry.set_published(
                PublicationRecord(run_date_msk=run_date_msk, published=True, tg_message_id=msg_id)
            )
        )
        return post, errors

    # load rules
    rules, rules_err = rules_loader.load()
    errors.extend(rules_err)

    # score+rank
    ranked = score_matches(matches, player_stats, rules)

    # load videos
    match_ids = [rm.match.match_id for rm in ranked if rm.rank.score >= cfg.min_interest_score]
    video_res, video_err = video_loader.load_for_matches(match_ids)
    errors.extend(video_err)

    # build public post
    post = build_post_public(run_date_msk, now_utc, ranked, video_res, cfg, extra_errors=errors)

    # render & publish
    text = render_post(post)
    msg_id, pub_err = publisher.publish(text)
    errors.extend(pub_err)

    # register published
    errors.extend(
        registry.set_published(
            PublicationRecord(
                run_date_msk=run_date_msk,
                published=True,
                tg_message_id=msg_id,
                published_at_utc=(
                    now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=timezone.utc)
                ),
            )
        )
    )

    # обновим post.errors финальным списком (на случай ошибок публикации/реестра)
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


class InMemoryPublicationRegistry:
    def __init__(self) -> None:
        self._store: Dict[date, PublicationRecord] = {}

    def get(self, run_date_msk: date) -> Tuple[Optional[PublicationRecord], List[AppError]]:
        return self._store.get(run_date_msk), []

    def set_published(self, record: PublicationRecord) -> List[AppError]:
        self._store[record.run_date_msk] = record
        return []


class MockResultsLoader:
    def load(self, window_start_utc: datetime, window_end_utc: datetime):
        # два "финальных" матча в окне
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


class MockVideoLoader:
    def load_for_matches(self, match_ids: List[str]):
        out: List[VideoLinkResult] = []
        for mid in match_ids:
            out.append(
                VideoLinkResult(
                    match_id=mid,
                    kind=VideoKind.HIGHLIGHTS,
                    status=VideoStatus.FOUND,
                    url=f"https://vk.example/{mid}/highlights",
                )
            )
            # допустим, полная запись иногда не находится
            out.append(
                VideoLinkResult(
                    match_id=mid,
                    kind=VideoKind.FULL,
                    status=VideoStatus.NOT_FOUND,
                    url=None,
                )
            )
        return out, []


class StdoutPublisher:
    def publish(self, text: str):
        print("----- PUBLISH -----")
        print(text)
        print("-------------------")
        return "mock_message_id", []


if __name__ == "__main__":
    cfg = UsecaseConfig(min_interest_score=0.0)
    now_utc = datetime.now(timezone.utc)

    post, errs = run_daily_digest(
        now_utc=now_utc,
        cfg=cfg,
        results_loader=MockResultsLoader(),
        rules_loader=MockScoringRulesLoader(),
        video_loader=MockVideoLoader(),
        publisher=StdoutPublisher(),
        registry=InMemoryPublicationRegistry(),
    )
