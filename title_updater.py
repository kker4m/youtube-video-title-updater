import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
INTERVAL_SECONDS = 60 * 60
PROJECT_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = PROJECT_DIR / "oauth_credentials.json"
TOKEN_FILE = PROJECT_DIR / "token.json"
DEFAULT_STATS_FILE = Path("/var/www/youtube-stats/stats.json")
THUMBNAIL_QUALITY_ORDER = ("maxres", "standard", "high", "medium", "default")


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
    response = youtube.videos().list(part="snippet,statistics", id=video_id).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError(f"Video bulunamadı: {video_id}")
    return items[0]


def fetch_channel(youtube) -> dict:
    response = (
        youtube.channels()
        .list(part="snippet,statistics,contentDetails", mine=True)
        .execute()
    )
    items = response.get("items", [])
    if not items:
        raise RuntimeError("OAuth hesabına bağlı YouTube kanalı bulunamadı.")
    return items[0]


def select_latest_public_video(video_ids: list[str], videos: list[dict]) -> dict:
    videos_by_id = {video.get("id"): video for video in videos}

    for video_id in video_ids:
        video = videos_by_id.get(video_id)
        if not video:
            continue

        status = video.get("status", {})
        snippet = video.get("snippet", {})
        if status.get("privacyStatus") != "public":
            continue
        if status.get("uploadStatus") != "processed":
            continue
        if snippet.get("liveBroadcastContent") == "upcoming":
            continue

        thumbnails = snippet.get("thumbnails", {})
        thumbnail_url = next(
            (
                thumbnails[quality]["url"]
                for quality in THUMBNAIL_QUALITY_ORDER
                if isinstance(thumbnails.get(quality), dict)
                and isinstance(thumbnails[quality].get("url"), str)
            ),
            None,
        )
        if not thumbnail_url:
            continue

        title = snippet.get("title")
        published_at = snippet.get("publishedAt")
        if not isinstance(title, str) or not isinstance(published_at, str):
            continue

        return {
            "id": video_id,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnailUrl": thumbnail_url,
            "publishedAt": published_at,
        }

    raise RuntimeError("Yayınlanmış güncel video bulunamadı.")


def fetch_latest_public_video(youtube, channel: dict) -> dict:
    uploads_playlist_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    playlist_response = (
        youtube.playlistItems()
        .list(part="contentDetails", playlistId=uploads_playlist_id, maxResults=10)
        .execute()
    )
    video_ids = [
        item["contentDetails"]["videoId"]
        for item in playlist_response.get("items", [])
        if item.get("contentDetails", {}).get("videoId")
    ]
    if not video_ids:
        raise RuntimeError("Kanalın yükleme listesinde video bulunamadı.")

    videos_response = (
        youtube.videos()
        .list(part="snippet,status", id=",".join(video_ids))
        .execute()
    )
    return select_latest_public_video(video_ids, videos_response.get("items", []))


def build_title(view_count: int) -> str:
    return f"Bu videonun {view_count} izlenmesi var"


def build_channel_stats(
    channel: dict, latest_video: dict, updated_at: datetime
) -> dict:
    statistics = channel["statistics"]
    snippet = channel["snippet"]
    subscriber_count = statistics.get("subscriberCount")

    return {
        "channelId": channel["id"],
        "title": snippet["title"],
        "subscriberCount": int(subscriber_count) if subscriber_count is not None else None,
        "viewCount": int(statistics.get("viewCount", 0)),
        "videoCount": int(statistics.get("videoCount", 0)),
        "latestVideo": latest_video,
        "updatedAt": updated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def publish_channel_stats(
    channel: dict, latest_video: dict, stats_file: Path
) -> dict:
    payload = build_channel_stats(channel, latest_video, datetime.now(timezone.utc))
    stats_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = stats_file.with_suffix(f"{stats_file.suffix}.tmp")
    temporary_file.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary_file.chmod(0o644)
    temporary_file.replace(stats_file)
    return payload


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


def run_cycle(youtube, video_id: str, stats_file: Path) -> None:
    checked_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    try:
        channel = fetch_channel(youtube)
        latest_video = fetch_latest_public_video(youtube, channel)
        stats = publish_channel_stats(channel, latest_video, stats_file)
        print(
            f"[{checked_at}] Kanal verisi güncellendi: "
            f"{stats['subscriberCount']} abone, {stats['viewCount']} izlenme, "
            f"{stats['videoCount']} video, son video {latest_video['id']}",
            flush=True,
        )
    except (HttpError, RuntimeError, KeyError, TypeError, ValueError, OSError) as error:
        print(f"Kanal verisi güncellenemedi: {error}", flush=True)

    try:
        video = fetch_video(youtube, video_id)
        view_count = int(video["statistics"].get("viewCount", 0))
        current_title = video["snippet"]["title"]
        new_title = build_title(view_count)

        if current_title == new_title:
            print(f"[{checked_at}] Başlıkta değişiklik yok: {new_title}", flush=True)
        else:
            update_title(youtube, video, new_title)
            print(
                f"[{checked_at}] Başlık güncellendi: "
                f"{current_title!r} -> {new_title!r}",
                flush=True,
            )
    except (HttpError, RuntimeError, KeyError, TypeError, ValueError) as error:
        print(f"Başlık güncellenemedi: {error}", flush=True)


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    video_id = os.getenv("YOUTUBE_VIDEO_ID", "").strip()
    if not video_id:
        raise SystemExit(".env içinde YOUTUBE_VIDEO_ID tanımlanmalı.")

    stats_file = Path(os.getenv("YOUTUBE_STATS_FILE", str(DEFAULT_STATS_FILE)))
    youtube = build("youtube", "v3", credentials=authorize(), cache_discovery=False)
    print("Başlık ve kanal verileri saatte bir güncellenecek.", flush=True)

    try:
        while True:
            run_cycle(youtube, video_id, stats_file)
            time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nUpdater durduruldu.")


if __name__ == "__main__":
    main()
