# contracts.py
# Минимальные data contracts для проекта NHL rewatch recommender.
# Принцип: "данные для расчета" могут содержать спойлеры (счет/статы),
# но "данные для публикации" (PostPublic) спойлеров НЕ содержат.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional

# -----------------------------
# Общие enum'ы и базовые типы
# -----------------------------


class Severity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class SourceSystem(str, Enum):
    NHL = "nhl"
    VK = "vk"
    TG = "tg"
    STORAGE = "storage"
    CORE = "core"


class VideoKind(str, Enum):
    HIGHLIGHTS = "highlights"
    FULL = "full"


class VideoStatus(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass(frozen=True)
class AppError:
    """Единый формат ошибки/предупреждения для пайплайна и поста."""

    code: str  # например: "NHL_TIMEOUT", "VK_AUTH", "DATA_INVALID"
    source: SourceSystem  # где произошло
    severity: Severity  # warning/error
    message: str  # коротко, по делу
    details: Optional[Dict[str, str]] = None  # для логов/диагностики (опционально)


@dataclass(frozen=True)
class Team:
    """Ссылка на команду."""

    team_id: str  # стабильный id из источника результатов
    name: str  # "Boston Bruins"
    abbr: Optional[str] = None  # "BOS"


# -----------------------------
# Контракты "для расчета" (могут содержать спойлеры)
# -----------------------------


@dataclass(frozen=True)
class MatchForScoring:
    """
    Матч как вход в скоринг.
    ВАЖНО: score_home/score_away — спойлерные поля.
    Они НИКОГДА не должны попадать в публичный вывод.
    """

    match_id: str
    start_time_utc: datetime  # всегда UTC (или трактуем как UTC)
    season: str  # например "2025-2026"
    home: Team
    away: Team

    # Спойлер: счет (нужен для твоей формулы интересности)
    score_home: int
    score_away: int

    # Опциональные признаки (если пригодятся)
    went_overtime: Optional[bool] = None
    went_shootout: Optional[bool] = None


@dataclass(frozen=True)
class PlayerGameStats:
    """
    Агрегированные статы игрока за матч (без таймлайна событий).
    Можно расширять, но лучше держать просто.
    """

    match_id: str
    player_id: str
    player_name: str
    team_id: str
    goals: int = 0
    assists: int = 0


@dataclass(frozen=True)
class ScoringRule:
    """
    Таблица "повышающих очков".
    entity_type: "team" или "player"
    entity_id: соответствующий id
    weight: добавка к индексу интересности
    """

    entity_type: str  # "team" | "player"
    entity_id: str
    weight: float
    label: Optional[str] = None  # человекочитаемая подпись (для дебага)


@dataclass(frozen=True)
class ScoreContribution:
    """
    Вклад конкретного правила/фактора в итоговый индекс.
    Это удобно для отладки и тестов.
    """

    reason: str  # например: "favorite_player", "overtime_bonus"
    weight: float
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None


@dataclass(frozen=True)
class MatchScore:
    """Индекс интересности матча + (опционально) разложение на вклады."""

    match_id: str
    score: float
    contributions: List[ScoreContribution] = field(default_factory=list)


@dataclass(frozen=True)
class RankedMatch:
    """Матч + его рейтинг (после скоринга/ранжирования)."""

    match: MatchForScoring
    rank: MatchScore


# -----------------------------
# Видео (результат video_load)
# -----------------------------


@dataclass(frozen=True)
class VideoLinkResult:
    """
    Результат поиска/получения видео для матча и типа ролика.
    Если FOUND -> url обязателен.
    Если NOT_FOUND -> url None, error None.
    Если ERROR -> error обязателен.
    """

    match_id: str
    kind: VideoKind
    status: VideoStatus
    url: Optional[str] = None
    error: Optional[AppError] = None
    source: SourceSystem = SourceSystem.VK


# -----------------------------
# Публичный вывод (без спойлеров)
# -----------------------------


@dataclass(frozen=True)
class PostItemPublic:
    """
    Одна строка/блок в посте.
    Спойлеров здесь быть не должно (никаких счетов/стат).
    """

    match_id: str
    title: str  # например "BOS — NYR"
    start_time_utc: datetime

    highlights_url: Optional[str] = None
    full_url: Optional[str] = None

    # Можно держать для сортировки/диагностики, но не печатать в текст
    rank_score: Optional[float] = None


@dataclass(frozen=True)
class PostPublic:
    """
    То, что публикуем (или могли бы опубликовать).
    run_date_msk — ключ "дня" для идемпотентности.
    """

    run_date_msk: date
    generated_at_utc: datetime
    items: List[PostItemPublic] = field(default_factory=list)
    errors: List[AppError] = field(default_factory=list)


# -----------------------------
# Идемпотентность/реестр публикаций (минимальный контракт)
# -----------------------------


@dataclass(frozen=True)
class PublicationRecord:
    """
    Запись о том, что пост за конкретную дату был опубликован.
    Реализация может быть через storage или через проверку Telegram — решите позже.
    """

    run_date_msk: date
    published: bool
    tg_message_id: Optional[str] = None
    published_at_utc: Optional[datetime] = None
