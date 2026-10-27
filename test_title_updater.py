import unittest
from datetime import datetime, timezone
from unittest import mock

import title_updater
from title_updater import (
    build_contents,
    build_title,
    parse_duration_seconds,
    select_latest_public_video,
)


class BuildTitleTest(unittest.TestCase):
    def test_uses_video_view_count(self) -> None:
        self.assertEqual(build_title(0), "Bu videonun 0 izlenmesi var")
        self.assertEqual(build_title(12_345), "Bu videonun 12345 izlenmesi var")


class LatestPublicVideoTest(unittest.TestCase):
    def test_skips_private_and_upcoming_uploads(self) -> None:
        videos = [
            {
                "id": "private",
                "status": {"privacyStatus": "private", "uploadStatus": "processed"},
                "snippet": {
                    "title": "Private video",
                    "publishedAt": "2026-09-13T12:00:00Z",
                    "liveBroadcastContent": "none",
                    "thumbnails": {"high": {"url": "https://example.com/private.jpg"}},
                },
            },
            {
                "id": "upcoming",
                "status": {"privacyStatus": "public", "uploadStatus": "processed"},
                "snippet": {
                    "title": "Upcoming stream",
                    "publishedAt": "2026-09-14T12:00:00Z",
                    "liveBroadcastContent": "upcoming",
                    "thumbnails": {"high": {"url": "https://example.com/upcoming.jpg"}},
                },
            },
            {
                "id": "published",
                "status": {"privacyStatus": "public", "uploadStatus": "processed"},
                "snippet": {
                    "title": "Published video",
                    "publishedAt": "2026-09-12T12:00:00Z",
                    "liveBroadcastContent": "none",
                    "thumbnails": {
                        "high": {"url": "https://example.com/high.jpg"},
                        "maxres": {"url": "https://example.com/maxres.jpg"},
                    },
                },
            },
        ]

        result = select_latest_public_video(
            ["private", "upcoming", "published"], videos
        )

        self.assertEqual(
            result,
            {
                "id": "published",
                "title": "Published video",
                "url": "https://www.youtube.com/watch?v=published",
                "thumbnailUrl": "https://example.com/maxres.jpg",
                "publishedAt": "2026-09-12T12:00:00Z",
            },
        )


class ParseDurationTest(unittest.TestCase):
    def test_parses_iso_8601_durations(self) -> None:
        self.assertEqual(parse_duration_seconds("PT59S"), 59)
        self.assertEqual(parse_duration_seconds("PT12M3S"), 723)
        self.assertEqual(parse_duration_seconds("PT1H2M"), 3720)
        self.assertEqual(parse_duration_seconds("P1DT1S"), 86401)
        self.assertIsNone(parse_duration_seconds("garbage"))


class BuildContentsTest(unittest.TestCase):
    def video(self, video_id: str, published_at: str, privacy: str = "public") -> dict:
        return {
            "id": video_id,
            "status": {"privacyStatus": privacy, "uploadStatus": "processed"},
            "snippet": {
                "title": f"Title {video_id}",
                "description": "Description",
                "publishedAt": published_at,
                "liveBroadcastContent": "none",
                "thumbnails": {"high": {"url": f"https://example.com/{video_id}.jpg"}},
            },
            "statistics": {"viewCount": "10", "likeCount": "2"},
            "contentDetails": {"duration": "PT30S"},
        }

    def test_lists_public_uploads_newest_first_with_types(self) -> None:
        channel = {
            "id": "UC123",
            "snippet": {"title": "Channel"},
            "statistics": {"subscriberCount": "5", "viewCount": "100"},
        }
        videos = [
            self.video("old", "2026-01-01T00:00:00Z"),
            self.video("hidden", "2026-03-01T00:00:00Z", privacy="private"),
            self.video("new", "2026-02-01T00:00:00Z"),
        ]

        with mock.patch.object(
            title_updater, "check_is_short", side_effect=lambda video_id, _: video_id == "new"
        ):
            result = build_contents(
                channel, videos, datetime(2026, 3, 2, tzinfo=timezone.utc)
            )

        self.assertEqual(result["counts"], {"total": 2, "videos": 1, "shorts": 1})
        self.assertEqual([item["id"] for item in result["items"]], ["new", "old"])
        self.assertEqual(result["items"][0]["type"], "short")
        self.assertEqual(result["items"][0]["url"], "https://www.youtube.com/shorts/new")
        self.assertEqual(result["items"][1]["url"], "https://www.youtube.com/watch?v=old")
        self.assertIsNone(result["items"][1]["commentCount"])
        self.assertEqual(result["updatedAt"], "2026-03-02T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
