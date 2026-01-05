import httpx


def main() -> None:
    with httpx.Client(timeout=10.0) as client:
        r = client.get("https://api.github.com")
        r.raise_for_status()
        data = r.json()
        print("status:", r.status_code)
        print("keys:", list(data.keys())[:5])


if __name__ == "__main__":
    main()
