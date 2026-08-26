import json
import os
import sys

import requests
from dotenv import load_dotenv

API_URL = "https://www.googleapis.com/youtube/v3/channels"


def main() -> None:
    load_dotenv()

    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    channel_id = os.getenv("YOUTUBE_CHANNEL_ID", "").strip()

    if not api_key or not channel_id:
        raise SystemExit(
            ".env içinde YOUTUBE_API_KEY ve YOUTUBE_CHANNEL_ID tanımlanmalı."
        )

    response = requests.get(
        API_URL,
        params={
            "key": api_key,
            "id": channel_id,
            "part": "snippet,statistics,contentDetails,status,brandingSettings",
        },
        timeout=30,
    )

    try:
        response.raise_for_status()
    except requests.HTTPError:
        print(response.text, file=sys.stderr)
        raise SystemExit(f"YouTube API isteği başarısız: HTTP {response.status_code}")

    data = response.json()
    if not data.get("items"):
        raise SystemExit(f"Kanal bulunamadı: {channel_id}")

    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
