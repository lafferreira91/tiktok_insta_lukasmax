"""Every filesystem location the project uses, derived from a single root.

Before this module the paths were literals spread across the CLI, which is how
the pilot's video id ended up hardcoded in four places. Deriving them from one
root also makes the whole pipeline testable against a temporary directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Paths:
    """Filesystem layout rooted at ``root``.

    Override the root with the ``LUKASMAX_ROOT`` environment variable, which is
    what the tests use to run against a scratch directory.
    """

    root: Path

    @classmethod
    def resolve(cls, root: Path | str | None = None) -> Paths:
        if root is None:
            root = os.environ.get("LUKASMAX_ROOT") or REPO_ROOT
        return cls(Path(root).expanduser().resolve())

    # -- directories ----------------------------------------------------
    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def media(self) -> Path:
        return self.root / "media"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def tiktok_dir(self) -> Path:
        return self.media / "tiktok"

    @property
    def ready_dir(self) -> Path:
        return self.media / "ready"

    @property
    def captions_dir(self) -> Path:
        return self.data / "captions"

    @property
    def media_reports_dir(self) -> Path:
        return self.reports / "media"

    # -- data files -----------------------------------------------------
    @property
    def inventory(self) -> Path:
        return self.data / "tiktok_inventory.json"

    @property
    def ranking_csv(self) -> Path:
        return self.data / "tiktok_ranking.csv"

    @property
    def downloaded(self) -> Path:
        return self.data / "downloaded.txt"

    @property
    def queue(self) -> Path:
        return self.data / "queue.json"

    @property
    def publish_log(self) -> Path:
        return self.data / "publish_log.jsonl"

    @property
    def slots(self) -> Path:
        return self.data / "slots.json"

    @property
    def insights_csv(self) -> Path:
        return self.data / "insights.csv"

    # -- report files ---------------------------------------------------
    @property
    def download_errors(self) -> Path:
        return self.reports / "download_errors.json"

    @property
    def status(self) -> Path:
        return self.reports / "status.json"

    # -- per-video files ------------------------------------------------
    def tiktok_source(self, video_id: str) -> Path:
        """The archived TikTok download, straight from yt-dlp."""
        return self.tiktok_dir / f"{video_id}.mp4"

    def tiktok_info(self, video_id: str) -> Path:
        """yt-dlp's sidecar metadata, the input for caption generation."""
        return self.tiktok_dir / f"{video_id}.info.json"

    def ready(self, video_id: str) -> Path:
        """The normalized copy that is safe to publish as a Reel."""
        return self.ready_dir / f"{video_id}.mp4"

    def media_report(self, video_id: str) -> Path:
        return self.media_reports_dir / f"{video_id}.json"

    def ready_report(self, video_id: str) -> Path:
        return self.media_reports_dir / f"{video_id}-ready.json"

    def caption(self, video_id: str) -> Path:
        return self.captions_dir / f"{video_id}.json"

    def frames_dir(self, video_id: str) -> Path:
        return self.reports / "frames" / video_id
