import os
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

API_URL = "https://www.googleapis.com/youtube/v3/videos"
INTERVAL_SECONDS = 30


def fetch_video(api_key: str, video_id: str) -> tuple[str, int]:
    response = requests.get(
        API_URL,
        params={
            "key": api_key,
            "id": video_id,
            "part": "snippet,statistics",
        },
        timeout=30,
    )
    response.raise_for_status()

    items = response.json().get("items", [])
    if not items:
        raise RuntimeError(f"Video bulunamadı: {video_id}")

    video = items[0]
    return video["snippet"]["title"], int(video["statistics"].get("viewCount", 0))


def main() -> None:
    load_dotenv()
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    video_id = os.getenv("YOUTUBE_VIDEO_ID", "").strip()

    if not api_key or not video_id:
        raise SystemExit(
            ".env içinde YOUTUBE_API_KEY ve YOUTUBE_VIDEO_ID tanımlanmalı."
        )

    print("Video 30 saniyede bir kontrol ediliyor. Durdurmak için Ctrl+C.")
    try:
        while True:
            try:
                title, view_count = fetch_video(api_key, video_id)
                checked_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                print(
                    f"[{checked_at}] Başlık: {title} | İzlenme: {view_count:,}",
                    flush=True,
                )
            except (requests.RequestException, RuntimeError, KeyError, ValueError) as error:
                print(f"Kontrol başarısız: {error}", flush=True)

            time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nTest durduruldu.")


if __name__ == "__main__":
    main()
