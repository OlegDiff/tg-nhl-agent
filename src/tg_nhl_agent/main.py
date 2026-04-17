from __future__ import annotations

import webbrowser
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

from tg_nhl_agent.core_scoring import build_post_public, score_and_rank
from tg_nhl_agent.favorites_loader import load_favorite_players
from tg_nhl_agent.nhl_api_results_loader import NhlApiResultsLoader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_HTML = OUTPUT_DIR / "nhl_digest.html"


def _render_html(
    *,
    generated_at_utc: datetime,
    window_start_utc: datetime,
    window_end_utc: datetime,
    favorites_count: int,
    matches_count: int,
    player_stats_count: int,
    errors_count: int,
    post,
) -> str:
    items_html: list[str] = []

    if not post.items:
        items_html.append(
            """
            <div class="empty">
              <h2>Матчей не найдено</h2>
              <p>За выбранное окно данных для показа нет.</p>
            </div>
            """
        )
    else:
        for idx, item in enumerate(post.items, start=1):
            items_html.append(
                f"""
                <div class="match-card">
                  <div class="match-num">{idx:02d}</div>
                  <div class="match-main">
                    <div class="match-title">{escape(item.title)}</div>
                    <div class="match-meta">
                      <span>UTC: {escape(item.start_time_utc.isoformat())}</span>
                      <span>score: {item.rank_score:.2f}</span>
                    </div>
                  </div>
                </div>
                """
            )

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>NHL Digest</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --card: #ffffff;
      --text: #18212f;
      --muted: #667085;
      --line: #dbe3ee;
      --accent: #1f6feb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    .page {{
      max-width: 980px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    .header {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 24px;
      margin-bottom: 20px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 28px;
      line-height: 1.2;
    }}
    .sub {{
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
      margin-bottom: 18px;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }}
    .stat {{
      background: #f9fbff;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px 16px;
    }}
    .stat-label {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 6px;
    }}
    .stat-value {{
      font-size: 22px;
      font-weight: 700;
    }}
    .section-title {{
      font-size: 20px;
      font-weight: 700;
      margin: 24px 0 12px;
    }}
    .match-card {{
      display: grid;
      grid-template-columns: 72px 1fr;
      gap: 14px;
      align-items: center;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px 18px;
      margin-bottom: 12px;
    }}
    .match-num {{
      width: 54px;
      height: 54px;
      border-radius: 14px;
      background: #eef4ff;
      border: 1px solid #cfe0ff;
      color: var(--accent);
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      font-size: 18px;
    }}
    .match-title {{
      font-size: 22px;
      font-weight: 700;
      margin-bottom: 6px;
    }}
    .match-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      color: var(--muted);
      font-size: 14px;
    }}
    .empty {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 24px;
    }}
  </style>
</head>
<body>
  <div class="page">
    <div class="header">
      <h1>NHL Digest</h1>
      <div class="sub">
        Сгенерировано: {escape(generated_at_utc.isoformat())}<br>
        Окно: {escape(window_start_utc.isoformat())} .. {escape(window_end_utc.isoformat())}
      </div>

      <div class="stats">
        <div class="stat">
          <div class="stat-label">Активных favorite players</div>
          <div class="stat-value">{favorites_count}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Матчей загружено</div>
          <div class="stat-value">{matches_count}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Player stats</div>
          <div class="stat-value">{player_stats_count}</div>
        </div>
        <div class="stat">
          <div class="stat-label">Ошибок</div>
          <div class="stat-value">{errors_count}</div>
        </div>
      </div>
    </div>

    <div class="section-title">RANKED MATCHES</div>
    {"".join(items_html)}
  </div>
</body>
</html>
"""


def run_dry() -> Path:
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

    print("\nRANKED MATCHES (debug, with score shown):")
    for i, it in enumerate(post.items, start=1):
        print(f"{i:02d}. {it.title} | {it.start_time_utc.isoformat()} | score={it.rank_score:.2f}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html = _render_html(
        generated_at_utc=now_utc,
        window_start_utc=window_start,
        window_end_utc=window_end,
        favorites_count=len(favorite_players),
        matches_count=len(res.matches),
        player_stats_count=len(res.player_stats),
        errors_count=len(res.errors),
        post=post,
    )
    OUTPUT_HTML.write_text(html, encoding="utf-8")

    return OUTPUT_HTML


def main() -> None:
    output_path = run_dry()
    print("\nhtml:", output_path)
    webbrowser.open(output_path.resolve().as_uri())


if __name__ == "__main__":
    main()
