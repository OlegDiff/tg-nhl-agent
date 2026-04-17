# Config & Secrets v1

Цель: зафиксировать **какие параметры нужны** для v1, **где они живут**, и **какие обязательны**.

## Источники конфигурации (рекомендовано)
1) `config.yaml` — **несекретные** настройки (дефолты).
2) `.env` — **секреты** и локальные оверрайды (НЕ коммитим).

**Правило приоритета:** переменные окружения (`.env`) **перекрывают** значения из `config.yaml`.

---

## Обязательные секреты (только .env)
- `TG_BOT_TOKEN` — токен бота Telegram.
- `TG_CHANNEL_ID` — id канала/чата (например `@my_channel` или числовой id).
- `VK_TOKEN` — токен VK API (если нужен для `video_load`).
- `NHL_API_KEY` — если выбранный источник NHL требует ключ (если нет — оставить пустым и не использовать).

---

## Несекретные параметры (config.yaml или env-override)
### Расписание/временные окна
- `TZ_MSK` (default: `Europe/Moscow`) — таймзона “операционного дня”.
- `PUBLISH_HOUR_MSK` (default: `8`) — час публикации по Москве.
- `LOOKBACK_HOURS` (default: `48`) — окно матчей: `[08:00 MSK − LOOKBACK_HOURS, 08:00 MSK)`.

### Ранжирование
- `MIN_INTEREST_SCORE` (default: `0.0`) — минимальный индекс интересности, ниже не публикуем.

### Режимы запуска
- `DRY_RUN` (default: `false`) — если `true`, **не публикуем в TG**, а только печатаем в лог/консоль.
- `MAX_MATCHES` (default: `20`) — верхняя граница числа матчей в посте (защита от “спама”).

### Надёжность/повторы
- `IDEMPOTENCY_BACKEND` (default: `file`) — где хранить факт публикации (`file` / `sqlite` / `tg_check` позже).
- `STATE_PATH` (default: `./state`) — папка для state-файлов/БД (если backend file/sqlite).

### Логи
- `LOG_LEVEL` (default: `INFO`)
- `LOG_PATH` (default: `./logs/app.log`) — если пишем в файл.

---

## Минимальный `config.yaml` (v1)
```yaml
TZ_MSK: Europe/Moscow
PUBLISH_HOUR_MSK: 8
LOOKBACK_HOURS: 48

MIN_INTEREST_SCORE: 0.0
MAX_MATCHES: 20
DRY_RUN: false

IDEMPOTENCY_BACKEND: file
STATE_PATH: ./state

LOG_LEVEL: INFO
LOG_PATH: ./logs/app.log
```

---

## Замечание про Windows и таймзоны
Если `ZoneInfo('Europe/Moscow')` падает с ошибкой про `tzdata`, установи пакет:
```bash
python -m pip install tzdata
```
