from __future__ import annotations

from typing import Any

import httpx


class BadAPIResponse(RuntimeError):
    """Ответ API не соответствует ожиданиям (не JSON или неправильная структура)."""


def fetch_json(url: str) -> dict[str, Any]:
    timeout = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)

    with httpx.Client(timeout=timeout) as client:
        r = client.get(url)
        r.raise_for_status()

        # 1) Пробуем JSON
        try:
            data = r.json()
        except ValueError as e:
            # Показываем кусочек тела, чтобы было видно, что пришло (например HTML)
            snippet = (r.text or "")[:200].replace("\n", " ")
            raise BadAPIResponse(f"Expected JSON but got non-JSON. Snippet: {snippet}") from e

        # 2) Проверяем тип
        if not isinstance(data, dict):
            raise BadAPIResponse(f"Expected dict JSON, got {type(data).__name__}")

        return data


def main() -> None:
    ok = fetch_json("https://api.github.com")
    print("ok keys:", list(ok.keys())[:5])

    # Специально плохой пример (скорее всего вернёт HTML, не JSON)
    try:
        fetch_json("https://example.com")
    except Exception as e:
        print("example.com error:", type(e).__name__, "-", str(e)[:120])


if __name__ == "__main__":
    main()
