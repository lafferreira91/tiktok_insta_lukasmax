"""The Graph API client: error classification and a genuinely resumable upload.

The previous implementation read the whole file into memory and always sent
``offset: 0``, so ``upload_type=resumable`` never resumed anything. These tests
pin the chunking down.
"""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from pathlib import Path

import pytest

from lukasmax_automation.instagram import (
    UPLOAD_CHUNK_BYTES,
    InstagramPublisher,
    PermanentError,
    RetriableError,
)


class FakeTransport:
    """Stands in for ``_request``, recording every call."""

    def __init__(self):
        self.calls: list[tuple[str, str, object, dict]] = []

    def __call__(self, method, url, data=None, headers=None):
        self.calls.append((method, url, data, headers or {}))
        if url.endswith("/media_publish"):
            return {"id": "media-1"}
        if url.endswith("/media"):
            return {"id": "container-1", "uri": "https://upload.test/container-1"}
        if "status_code" in url:
            return {"status_code": "FINISHED"}
        return {}


class TestAccount:
    def test_check_account_is_a_read_only_get(self):
        publisher = InstagramPublisher("user-1", "secret")
        transport = FakeTransport()
        publisher._request = transport

        publisher.check_account()

        method, url, _, _ = transport.calls[0]
        assert method == "GET"
        assert "access_token=secret" in url


class TestContainer:
    def test_url_container_hands_meta_the_link(self):
        publisher = InstagramPublisher("user-1", "secret")
        transport = FakeTransport()
        publisher._request = transport

        publisher.create_container_from_url("https://cdn.test/v.mp4", "legenda")

        method, url, data, _ = transport.calls[0]
        assert method == "POST"
        assert url.endswith("/user-1/media")
        assert data["media_type"] == "REELS"
        assert data["video_url"] == "https://cdn.test/v.mp4"
        assert "upload_type" not in data, "o caminho por URL nao usa upload resumivel"

    def test_expired_container_is_permanent_not_retriable(self):
        publisher = InstagramPublisher("user-1", "secret")
        publisher._request = lambda *a, **k: {"status_code": "EXPIRED"}

        with pytest.raises(PermanentError):
            publisher.wait_until_ready("container-1", timeout_seconds=1)


class TestResumableUpload:
    def _video(self, tmp_path: Path, size: int) -> Path:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * size)
        return video

    def test_uploads_in_contiguous_chunks(self, tmp_path):
        size = UPLOAD_CHUNK_BYTES * 3 + 1234
        video = self._video(tmp_path, size)
        publisher = InstagramPublisher("user-1", "secret")
        offsets, lengths = [], []

        def transport(method, url, data=None, headers=None):
            offsets.append(int(headers["offset"]))
            lengths.append(len(data))
            return {"offset": int(headers["offset"]) + len(data)}

        publisher._request = transport
        result = publisher.upload("https://upload.test/c1", video)

        assert result["offset"] == size
        assert offsets == [0, UPLOAD_CHUNK_BYTES, UPLOAD_CHUNK_BYTES * 2, UPLOAD_CHUNK_BYTES * 3]
        assert sum(lengths) == size
        assert max(lengths) <= UPLOAD_CHUNK_BYTES, "leu mais que um chunk de uma vez"

    def test_never_reads_the_whole_file_into_memory(self, tmp_path):
        video = self._video(tmp_path, UPLOAD_CHUNK_BYTES * 2)
        publisher = InstagramPublisher("user-1", "secret")
        sizes = []

        def transport(method, url, data=None, headers=None):
            sizes.append(len(data))
            return {"offset": int(headers["offset"]) + len(data)}

        publisher._request = transport
        publisher.upload("https://upload.test/c1", video)

        assert all(size <= UPLOAD_CHUNK_BYTES for size in sizes)

    def test_resumes_from_the_offset_meta_confirms(self, tmp_path, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _: None)
        size = UPLOAD_CHUNK_BYTES * 3
        video = self._video(tmp_path, size)
        publisher = InstagramPublisher("user-1", "secret")
        attempts = {"count": 0}
        seen_offsets = []

        def transport(method, url, data=None, headers=None):
            if method == "GET":
                # Meta reports it kept exactly one chunk.
                return {"offset": UPLOAD_CHUNK_BYTES}
            offset = int(headers["offset"])
            seen_offsets.append(offset)
            attempts["count"] += 1
            if attempts["count"] == 2:
                raise RetriableError("conexao caiu no meio")
            return {"offset": offset + len(data)}

        publisher._request = transport
        result = publisher.upload("https://upload.test/c1", video)

        assert result["offset"] == size
        assert UPLOAD_CHUNK_BYTES in seen_offsets, "nao retomou do offset confirmado"
        assert seen_offsets == sorted(seen_offsets), "os offsets andaram para tras"

    def test_gives_up_after_max_attempts(self, tmp_path, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _: None)
        video = self._video(tmp_path, UPLOAD_CHUNK_BYTES)
        publisher = InstagramPublisher("user-1", "secret")

        def always_fails(method, url, data=None, headers=None):
            if method == "GET":
                return {"offset": 0}
            raise RetriableError("500")

        publisher._request = always_fails
        with pytest.raises(RetriableError):
            publisher.upload("https://upload.test/c1", video, max_attempts=3)


class TestErrorClassification:
    @pytest.mark.parametrize(
        "status,payload,expected",
        [
            (500, {}, RetriableError),
            (429, {}, RetriableError),
            (400, {"error": {"code": 190, "message": "token invalido"}}, PermanentError),
            (400, {"error": {"code": 4, "message": "rate limit"}}, RetriableError),
            (400, {"error": {"code": 9999, "message": "midia recusada"}}, PermanentError),
        ],
    )
    def test_status_and_code_decide_whether_to_retry(self, status, payload, expected, monkeypatch):
        publisher = InstagramPublisher("user-1", "secret")
        body = json.dumps(payload).encode()

        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                getattr(request, "full_url", "https://x"), status, "erro", {}, BytesIO(body)
            )

        monkeypatch.setattr("urllib.request.urlopen", urlopen)
        with pytest.raises(expected):
            publisher.check_account()

    def test_error_message_from_meta_is_surfaced(self, monkeypatch):
        publisher = InstagramPublisher("user-1", "secret")
        body = json.dumps(
            {"error": {"code": 9004, "message": "O video nao pode ser baixado da URL"}}
        ).encode()

        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError("https://x", 400, "Bad Request", {}, BytesIO(body))

        monkeypatch.setattr("urllib.request.urlopen", urlopen)
        with pytest.raises(PermanentError, match="nao pode ser baixado"):
            publisher.check_account()

    def test_network_failure_is_retriable(self, monkeypatch):
        publisher = InstagramPublisher("user-1", "secret")

        def urlopen(request, timeout=None):
            raise urllib.error.URLError("conexao recusada")

        monkeypatch.setattr("urllib.request.urlopen", urlopen)
        with pytest.raises(RetriableError):
            publisher.check_account()
