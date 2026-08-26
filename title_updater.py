import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
INTERVAL_SECONDS = 10 * 60
PROJECT_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = PROJECT_DIR / "oauth_credentials.json"
TOKEN_FILE = PROJECT_DIR / "token.json"


def authorize() -> Credentials:
    credentials = None

    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise SystemExit(
                    "oauth_credentials.json bulunamadı. Google Cloud'dan OAuth Desktop "
                    "client dosyasını indirip bu klasöre koy."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            credentials = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")

    return credentials


def fetch_video(youtube, video_id: str) -> dict:
    response = (
        youtube.videos()
        .list(part="snippet,statistics", id=video_id)
        .execute()
    )
    items = response.get("items", [])
    if not items:
        raise RuntimeError(f"Video bulunamadı: {video_id}")
    return items[0]


def build_title(view_count: int) -> str:
    return f"{view_count} izlenmeli test videom"


def update_title(youtube, video: dict, new_title: str) -> None:
    old_snippet = video["snippet"]
    snippet = {
        "title": new_title,
        "description": old_snippet.get("description", ""),
        "categoryId": old_snippet["categoryId"],
    }

    for optional_field in ("tags", "defaultLanguage"):
        if optional_field in old_snippet:
            snippet[optional_field] = old_snippet[optional_field]

    (
        youtube.videos()
        .update(
            part="snippet",
            body={"id": video["id"], "snippet": snippet},
        )
        .execute()
    )


def main() -> None:
    load_dotenv()
    video_id = os.getenv("YOUTUBE_VIDEO_ID", "").strip()
    if not video_id:
        raise SystemExit(".env içinde YOUTUBE_VIDEO_ID tanımlanmalı.")

    youtube = build("youtube", "v3", credentials=authorize(), cache_discovery=False)
    print("Başlık 10 dakikada bir güncellenecek. Durdurmak için Ctrl+C.")

    try:
        while True:
            try:
                video = fetch_video(youtube, video_id)
                view_count = int(video["statistics"].get("viewCount", 0))
                current_title = video["snippet"]["title"]
                new_title = build_title(view_count)
                checked_at = datetime.now().astimezone().strftime(
                    "%Y-%m-%d %H:%M:%S %Z"
                )

                if current_title == new_title:
                    print(
                        f"[{checked_at}] Değişiklik yok: {new_title}",
                        flush=True,
                    )
                else:
                    update_title(youtube, video, new_title)
                    print(
                        f"[{checked_at}] Başlık güncellendi: "
                        f"{current_title!r} -> {new_title!r}",
                        flush=True,
                    )
            except (HttpError, RuntimeError, KeyError, TypeError, ValueError) as error:
                print(f"Güncelleme başarısız: {error}", flush=True)

            time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nUpdater durduruldu.")


if __name__ == "__main__":
    main()
