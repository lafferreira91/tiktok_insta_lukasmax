from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lukasmax_automation import queue as queue_mod
from lukasmax_automation.paths import Paths


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    """A scratch project layout, so tests never touch the real data/ or media/."""
    monkeypatch.setenv("LUKASMAX_ROOT", str(tmp_path))
    resolved = Paths.resolve()
    for directory in (resolved.data, resolved.tiktok_dir, resolved.ready_dir, resolved.reports):
        directory.mkdir(parents=True, exist_ok=True)
    return resolved


def make_item(
    tiktok_id: str = "111",
    *,
    status: str = "scheduled",
    minutes_from_now: int = -5,
    **overrides,
) -> dict:
    """A queue item due five minutes ago by default."""
    moment = datetime.now(UTC) + timedelta(minutes=minutes_from_now)
    item = queue_mod.new_item(
        tiktok_id=tiktok_id,
        source_url=f"https://www.tiktok.com/@_lukasmax/video/{tiktok_id}",
        scheduled_at=moment.isoformat(),
        scheduled_at_utc=moment.isoformat(),
        slot_id="test-slot",
        rank=0,
    )
    item["status"] = status
    item["caption"] = "legenda de teste"
    item["caption_fingerprint"] = "sha256:test"
    item["media"] = {
        "local_path": f"media/ready/{tiktok_id}.mp4",
        "asset_url": f"https://example.invalid/{tiktok_id}.mp4",
        "asset_name": f"{tiktok_id}.mp4",
        "release_tag": "media-v1",
        "sha256": "0" * 64,
        "bytes": 1024,
        "duration_seconds": 30.0,
    }
    item.update(overrides)
    return item


def make_queue(*items: dict) -> dict:
    return {"version": queue_mod.SCHEMA_VERSION, "items": list(items)}


def write_queue(path: Path, queue: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class FakePublisher:
    """Records every Graph API call so tests can assert what actually happened.

    The point of most P0 tests is a negative: that ``media_publish`` was called
    exactly once, never twice.
    """

    def __init__(self, *, status_code: str = "FINISHED", fail_publish: bool = False):
        self.calls: list[tuple[str, tuple, dict]] = []
        # Not named container_status: that would shadow the method below.
        self.status_code = status_code
        self.fail_publish = fail_publish
        self._next_container = 0
        self.recent_media: list[dict] = []

    def _record(self, name: str, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)

    # -- Graph API surface ---------------------------------------------
    def create_container_from_url(self, video_url: str, caption: str, **kwargs):
        # Os kwargs sao registrados de proposito: thumb_offset viaja por aqui, e
        # descarta-los faria um teste passar mesmo se a capa parasse de ser
        # enviada -- que foi como o quadro 0 chegou ao primeiro post.
        self._record("create_container_from_url", video_url, caption, **kwargs)
        self._next_container += 1
        return {"id": f"container-{self._next_container}"}

    def wait_until_ready(self, container_id: str, **kwargs) -> None:
        self._record("wait_until_ready", container_id)

    def container_status(self, container_id: str) -> dict:
        self._record("container_status", container_id)
        return {"status_code": self.status_code}

    def publish(self, container_id: str) -> dict:
        self._record("publish", container_id)
        if self.fail_publish:
            raise RuntimeError("publish falhou")
        return {"id": f"media-{container_id}"}

    def content_publishing_limit(self) -> dict:
        self._record("content_publishing_limit")
        return {"quota_usage": 0, "config": {"quota_total": 25}}

    def list_recent_media(self, limit: int = 10) -> list[dict]:
        self._record("list_recent_media", limit)
        return self.recent_media

    def permalink(self, media_id: str) -> str | None:
        self._record("permalink", media_id)
        return f"https://instagram.com/reel/{media_id}"


@pytest.fixture
def publisher() -> FakePublisher:
    return FakePublisher()
