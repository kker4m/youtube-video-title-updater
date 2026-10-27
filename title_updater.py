import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
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
DEFAULT_CONTENTS_FILE = Path("/var/www/youtube-stats/contents.json")
THUMBNAIL_QUALITY_ORDER = ("maxres", "standard", "high", "medium", "default")
# YouTube Shorts can be up to three minutes long; used only when the
# /shorts/ URL check cannot be completed.
SHORTS_MAX_SECONDS = 180
DURATION_PATTERN = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)
# Shorts status never changes after upload, so it is checked once per video.
shorts_cache: dict[str, bool] = {}


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


def select_thumbnail_url(snippet: dict) -> str | None:
    thumbnails = snippet.get("thumbnails", {})
    return next(
        (
            thumbnails[quality]["url"]
            for quality in THUMBNAIL_QUALITY_ORDER
            if isinstance(thumbnails.get(quality), dict)
            and isinstance(thumbnails[quality].get("url"), str)
        ),
        None,
    )


def is_public_upload(video: dict) -> bool:
    status = video.get("status", {})
    snippet = video.get("snippet", {})
    return (
        status.get("privacyStatus") == "public"
        and status.get("uploadStatus") == "processed"
        and snippet.get("liveBroadcastContent") != "upcoming"
    )


def select_latest_public_video(video_ids: list[str], videos: list[dict]) -> dict:
    videos_by_id = {video.get("id"): video for video in videos}

    for video_id in video_ids:
        video = videos_by_id.get(video_id)
        if not video:
            continue

        snippet = video.get("snippet", {})
        if not is_public_upload(video):
            continue

        thumbnail_url = select_thumbnail_url(snippet)
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


def fetch_all_upload_ids(youtube, channel: dict) -> list[str]:
    uploads_playlist_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    video_ids: list[str] = []
    page_token = None

    while True:
        playlist_response = (
            youtube.playlistItems()
            .list(
                part="contentDetails",
                playlistId=uploads_playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
            .execute()
        )
        video_ids.extend(
            item["contentDetails"]["videoId"]
            for item in playlist_response.get("items", [])
            if item.get("contentDetails", {}).get("videoId")
        )
        page_token = playlist_response.get("nextPageToken")
        if not page_token:
            return video_ids


def fetch_video_details(youtube, video_ids: list[str]) -> list[dict]:
    videos: list[dict] = []
    for start in range(0, len(video_ids), 50):
        videos_response = (
            youtube.videos()
            .list(
                part="snippet,status,statistics,contentDetails",
                id=",".join(video_ids[start : start + 50]),
            )
            .execute()
        )
        videos.extend(videos_response.get("items", []))
    return videos


def parse_duration_seconds(duration: str) -> int | None:
    match = DURATION_PATTERN.match(duration or "")
    if not match:
        return None
    parts = {name: int(value or 0) for name, value in match.groupdict().items()}
    return (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


def check_is_short(video_id: str, duration_seconds: int | None) -> bool:
    if video_id in shorts_cache:
        return shorts_cache[video_id]

    try:
        # youtube.com/shorts/<id> answers 200 for Shorts and redirects to
        # /watch for regular videos.
        response = requests.head(
            f"https://www.youtube.com/shorts/{video_id}",
            allow_redirects=False,
            timeout=10,
        )
    except requests.RequestException:
        return duration_seconds is not None and duration_seconds <= SHORTS_MAX_SECONDS

    if response.status_code == 200:
        shorts_cache[video_id] = True
    elif 300 <= response.status_code < 400:
        shorts_cache[video_id] = False
    else:
        return duration_seconds is not None and duration_seconds <= SHORTS_MAX_SECONDS
    return shorts_cache[video_id]


def build_content_item(video: dict) -> dict:
    video_id = video["id"]
    snippet = video["snippet"]
    statistics = video.get("statistics", {})
    duration_seconds = parse_duration_seconds(
        video.get("contentDetails", {}).get("duration", "")
    )
    is_short = check_is_short(video_id, duration_seconds)

    def optional_count(name: str) -> int | None:
        value = statistics.get(name)
        return int(value) if value is not None else None

    return {
        "id": video_id,
        "type": "short" if is_short else "video",
        "title": snippet["title"],
        "description": snippet.get("description", ""),
        "publishedAt": snippet["publishedAt"],
        "durationSeconds": duration_seconds,
        "url": (
            f"https://www.youtube.com/shorts/{video_id}"
            if is_short
            else f"https://www.youtube.com/watch?v={video_id}"
        ),
        "thumbnailUrl": select_thumbnail_url(snippet),
        "tags": snippet.get("tags", []),
        "viewCount": optional_count("viewCount"),
        "likeCount": optional_count("likeCount"),
        "commentCount": optional_count("commentCount"),
    }


def build_contents(channel: dict, videos: list[dict], updated_at: datetime) -> dict:
    items = sorted(
        (build_content_item(video) for video in videos if is_public_upload(video)),
        key=lambda item: item["publishedAt"],
        reverse=True,
    )
    shorts_count = sum(1 for item in items if item["type"] == "short")
    subscriber_count = channel["statistics"].get("subscriberCount")

    return {
        "channel": {
            "id": channel["id"],
            "title": channel["snippet"]["title"],
            "url": f"https://www.youtube.com/channel/{channel['id']}",
            "subscriberCount": int(subscriber_count) if subscriber_count is not None else None,
            "viewCount": int(channel["statistics"].get("viewCount", 0)),
        },
        "counts": {
            "total": len(items),
            "videos": len(items) - shorts_count,
            "shorts": shorts_count,
        },
        "updatedAt": updated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": items,
    }


def publish_contents(youtube, channel: dict, contents_file: Path) -> dict:
    video_ids = fetch_all_upload_ids(youtube, channel)
    videos = fetch_video_details(youtube, video_ids)
    payload = build_contents(channel, videos, datetime.now(timezone.utc))
    write_json_atomic(payload, contents_file)
    return payload


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
    write_json_atomic(payload, stats_file)
    return payload


def write_json_atomic(payload: dict, target_file: Path) -> None:
    target_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = target_file.with_suffix(f"{target_file.suffix}.tmp")
    temporary_file.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary_file.chmod(0o644)
    temporary_file.replace(target_file)


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


def run_cycle(youtube, video_id: str, stats_file: Path, contents_file: Path) -> None:
    checked_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    channel = None

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

    if channel is not None:
        try:
            contents = publish_contents(youtube, channel, contents_file)
            counts = contents["counts"]
            print(
                f"[{checked_at}] İçerik listesi güncellendi: "
                f"{counts['videos']} video, {counts['shorts']} shorts",
                flush=True,
            )
        except (HttpError, RuntimeError, KeyError, TypeError, ValueError, OSError) as error:
            print(f"İçerik listesi güncellenemedi: {error}", flush=True)

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
    contents_file = Path(os.getenv("YOUTUBE_CONTENTS_FILE", str(DEFAULT_CONTENTS_FILE)))
    youtube = build("youtube", "v3", credentials=authorize(), cache_discovery=False)
    print("Başlık ve kanal verileri saatte bir güncellenecek.", flush=True)

    try:
        while True:
            run_cycle(youtube, video_id, stats_file, contents_file)
            time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nUpdater durduruldu.")


if __name__ == "__main__":
    main()
