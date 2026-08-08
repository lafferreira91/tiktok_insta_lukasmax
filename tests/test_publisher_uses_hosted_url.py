"""The publish job must not touch the video.

The old ``publish_due`` re-downloaded from TikTok and re-ran ffmpeg at publish
time, putting the least reliable dependency in the system on the critical path
of every post. These tests make that regression impossible: yt-dlp, ffmpeg and
PyAV are all rigged to explode, and publishing still has to work.
"""

from __future__ import annotations

import subprocess

import pytest
from conftest import make_item, make_queue

from lukasmax_automation import publisher as publisher_mod
from lukasmax_automation import queue as queue_mod


@pytest.fixture(autouse=True)
def publishing_on(monkeypatch):
    monkeypatch.setenv("PUBLISH_ENABLED", "true")


@pytest.fixture
def sabotaged(monkeypatch):
    """Make every media-handling path fail loudly if it is reached."""

    def forbidden(*args, **kwargs):
        raise AssertionError("o job de publicacao nao pode tocar no arquivo de video")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    import lukasmax_automation.tiktok as tiktok_mod

    monkeypatch.setattr(tiktok_mod, "download_without_watermark", forbidden)
    import lukasmax_automation.media as media_mod

    monkeypatch.setattr(media_mod, "normalize_for_instagram", forbidden)
    monkeypatch.setattr(media_mod, "inspect", forbidden)


def test_publishes_from_the_hosted_url_alone(paths, publisher, sabotaged):
    queue_mod.save_queue(make_queue(make_item("111")), paths.queue)

    result = publisher_mod.publish_due(paths.queue, paths, publisher=publisher)

    assert len(result["published"]) == 1
    url_call = next(call for call in publisher.calls if call[0] == "create_container_from_url")
    assert url_call[1][0] == "https://example.invalid/111.mp4"


def test_publisher_module_does_not_import_media_or_tiktok():
    """A regression here would drag ffmpeg and yt-dlp back into the CI install."""
    source = (publisher_mod.__file__).replace(".pyc", ".py")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "from .tiktok import" not in text
    assert "from .media import" not in text


def test_missing_asset_url_fails_without_falling_back_to_download(paths, publisher, sabotaged):
    item = make_item("111")
    item["media"] = {"local_path": "media/ready/111.mp4"}  # sem asset_url
    queue_mod.save_queue(make_queue(item), paths.queue)

    result = publisher_mod.publish_due(paths.queue, paths, publisher=publisher)

    assert result["failed"], "deveria falhar em vez de tentar baixar o video"
    stored = queue_mod.load_queue(paths.queue)["items"][0]
    assert stored["status"] == "failed"
    assert "host-media" in (stored["last_error"] or "")
