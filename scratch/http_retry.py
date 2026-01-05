from __future__ import annotations

import time
from typing import Any

import httpx


def get_json_with_retries(url: str, *, attempts: int = 4) -> dict[str, Any]:
    timeout = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)

    last_exc: Exception | None = None
    with httpx.Client(timeout=timeout) as client:
        for i in range(1, attempts + 1):
            try:
                r = client.get(url)
                # 1) HTTP ошибки
                if r.status_code >= 500:
                    # серверная ошибка — можно повторить
                    raise httpx.HTTPStatusError(
                        f"Server error {r.status_code}",
                        request=r.request,
                        response=r,
                    )
                r.raise_for_status()

                # 2) Парсим JSON
                return r.json()

            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as e:
                last_exc = e
                # backoff: 0.5, 1.0, 2.0, ...
                sleep_s = 0.5 * (2 ** (i - 1))
                print(f"Attempt {i}/{attempts} failed: {type(e).__name__}. Sleep {sleep_s:.1f}s")
                if i < attempts:
                    time.sleep(sleep_s)
                else:
                    break

    assert last_exc is not None
    raise last_exc


def main() -> None:
    data = get_json_with_retries("https://api.github.com")
    print("ok keys:", list(data.keys())[:5])


if __name__ == "__main__":
    main()
