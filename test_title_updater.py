import unittest

from title_updater import build_title, select_latest_public_video


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


if __name__ == "__main__":
    unittest.main()
