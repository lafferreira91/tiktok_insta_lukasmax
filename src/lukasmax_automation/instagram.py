"""Client for the Instagram Content Publishing API (Instagram API with Login).

Two things about this API shape the design:

* There is no native scheduling. ``media_publish`` always posts immediately, so
  the schedule has to be an external cron -- see :mod:`.publisher`.
* ``media_publish`` is not idempotent. Retrying blindly after an ambiguous
  failure double-posts, which is why :meth:`list_recent_media` exists: it lets
  the caller check whether a crashed run had already succeeded.

Uses ``graph.instagram.com`` (not ``graph.facebook.com``), the right base for a
Creator account with no linked Facebook Page.
"""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .net import ssl_context

DEFAULT_API_VERSION = "v25.0"

#: Chunk size for resumable uploads. Small enough to keep memory flat, large
#: enough that a 60 MB Reel is a couple dozen requests.
UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024


class InstagramError(RuntimeError):
    """A Graph API call failed, with the API's own error payload attached."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload or {}

    @property
    def code(self) -> int | None:
        value = self.payload.get("error", {}).get("code")
        return int(value) if value is not None else None

    @property
    def subcode(self) -> int | None:
        value = self.payload.get("error", {}).get("error_subcode")
        return int(value) if value is not None else None


class RetriableError(InstagramError):
    """Transient: rate limit, server error, timeout. Worth trying again later."""


class PermanentError(InstagramError):
    """Will fail identically on retry -- bad token, rejected media, bad request."""


#: Graph API error codes that mean "slow down" or "we broke", not "you are wrong".
_RETRIABLE_CODES = {1, 2, 4, 17, 32, 341, 613}
#: Codes that mean the token is unusable; retrying only burns attempts.
_AUTH_CODES = {102, 190, 200, 10, 803}


def _classify(status: int | None, payload: dict[str, Any], message: str) -> InstagramError:
    code = payload.get("error", {}).get("code")
    code = int(code) if code is not None else None
    if code in _AUTH_CODES:
        return PermanentError(message, status=status, payload=payload)
    if code in _RETRIABLE_CODES:
        return RetriableError(message, status=status, payload=payload)
    if status is not None and (status >= 500 or status == 429):
        return RetriableError(message, status=status, payload=payload)
    if status is not None and 400 <= status < 500:
        return PermanentError(message, status=status, payload=payload)
    return RetriableError(message, status=status, payload=payload)


class InstagramPublisher:
    def __init__(
        self,
        user_id: str,
        access_token: str,
        api_version: str = DEFAULT_API_VERSION,
        *,
        timeout: int = 300,
    ):
        self.user_id = user_id
        self.access_token = access_token
        self.api_version = api_version
        self.base = f"https://graph.instagram.com/{api_version}"
        self.timeout = timeout
        #: Populated from X-Business-Use-Case-Usage so callers can log throttling.
        self.last_usage_header: str | None = None

    # -- transport ------------------------------------------------------
    def _request(
        self,
        method: str,
        url: str,
        data: dict[str, Any] | bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        headers = dict(headers or {})
        body = data
        if isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode()
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=ssl_context()
            ) as response:
                self.last_usage_header = response.headers.get("X-Business-Use-Case-Usage")
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            # Without reading the body the CI only ever sees "HTTP Error 400",
            # which says nothing about what Meta actually rejected.
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(error.read() or b"{}")
            except (json.JSONDecodeError, ValueError):
                payload = {}
            detail = payload.get("error", {}).get("message") or error.reason
            raise _classify(
                error.code, payload, f"{method} {url.split('?')[0]} -> {error.code}: {detail}"
            ) from error
        except urllib.error.URLError as error:
            raise RetriableError(
                f"Falha de rede em {method} {url.split('?')[0]}: {error.reason}"
            ) from error

    def _get(self, path: str, **params: Any) -> Any:
        query = urllib.parse.urlencode({**params, "access_token": self.access_token})
        return self._request("GET", f"{self.base}/{path}?{query}")

    def _post(self, path: str, **fields: Any) -> Any:
        return self._request(
            "POST", f"{self.base}/{path}", {**fields, "access_token": self.access_token}
        )

    # -- account --------------------------------------------------------
    def check_account(self) -> dict[str, Any]:
        return self._get(self.user_id, fields="id,user_id,username,account_type")

    def content_publishing_limit(self) -> dict[str, Any]:
        """Meta's own 24h quota. The only view that sees posts made from the phone."""
        response = self._get(
            f"{self.user_id}/content_publishing_limit", fields="config,quota_usage"
        )
        entries = response.get("data") or []
        return entries[0] if entries else {}

    def list_recent_media(self, limit: int = 10) -> list[dict[str, Any]]:
        """Recently published media, used to tell a crashed publish from a lost one."""
        response = self._get(f"{self.user_id}/media", fields="id,timestamp,permalink", limit=limit)
        return response.get("data") or []

    def media_insights(self, media_id: str, metrics: str) -> dict[str, Any]:
        return self._get(f"{media_id}/insights", metric=metrics)

    def permalink(self, media_id: str) -> str | None:
        try:
            return self._get(media_id, fields="permalink").get("permalink")
        except InstagramError:
            # Cosmetic only -- never fail a successful publish over it.
            return None

    def refresh_long_lived_token(self) -> dict[str, Any]:
        """Extend the 60-day token by another 60. Needs a token older than 24h."""
        query = urllib.parse.urlencode(
            {"grant_type": "ig_refresh_token", "access_token": self.access_token}
        )
        return self._request("GET", f"https://graph.instagram.com/refresh_access_token?{query}")

    # -- containers -----------------------------------------------------
    def create_container_from_url(
        self, video_url: str, caption: str, *, share_to_feed: bool = True
    ) -> dict[str, Any]:
        """Primary path: hand Meta a public URL and let it fetch the file itself.

        The runner never transfers the video, which keeps the publish job free of
        yt-dlp, ffmpeg and bandwidth.
        """
        return self._post(
            f"{self.user_id}/media",
            media_type="REELS",
            video_url=video_url,
            caption=caption,
            share_to_feed="true" if share_to_feed else "false",
        )

    def create_container_resumable(
        self, caption: str, *, share_to_feed: bool = True
    ) -> dict[str, Any]:
        """Fallback path: upload the bytes ourselves via :meth:`upload`."""
        return self._post(
            f"{self.user_id}/media",
            media_type="REELS",
            upload_type="resumable",
            caption=caption,
            share_to_feed="true" if share_to_feed else "false",
        )

    def upload_uri_for(self, container: dict[str, Any]) -> str:
        return container.get("uri") or (
            f"https://rupload.facebook.com/ig-api-upload/{self.api_version}/{container['id']}"
        )

    def upload(self, upload_uri: str, video: Path, *, max_attempts: int = 5) -> dict[str, Any]:
        """Upload a file in chunks, resuming from the offset Meta confirms.

        The previous implementation read the whole file into memory and always
        sent ``offset: 0``, so ``upload_type=resumable`` never actually resumed.
        """
        size = video.stat().st_size
        offset = 0
        attempt = 0
        with video.open("rb") as handle:
            while offset < size:
                handle.seek(offset)
                chunk = handle.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                try:
                    response = self._request(
                        "POST",
                        upload_uri,
                        chunk,
                        {
                            "Authorization": f"OAuth {self.access_token}",
                            "Content-Type": mimetypes.guess_type(video.name)[0] or "video/mp4",
                            "offset": str(offset),
                            "file_size": str(size),
                        },
                    )
                except RetriableError:
                    attempt += 1
                    if attempt >= max_attempts:
                        raise
                    time.sleep(min(30, 2**attempt))
                    # Ask Meta how much it actually kept rather than assuming the
                    # failed chunk landed (or did not).
                    offset = self._confirmed_offset(upload_uri, fallback=offset)
                    continue
                attempt = 0
                offset = int(response.get("offset") or offset + len(chunk))
        return {"offset": offset, "file_size": size}

    def _confirmed_offset(self, upload_uri: str, *, fallback: int) -> int:
        try:
            response = self._request(
                "GET", upload_uri, None, {"Authorization": f"OAuth {self.access_token}"}
            )
            return int(response.get("offset", fallback))
        except InstagramError:
            return fallback

    # -- status and publish ---------------------------------------------
    def container_status(self, container_id: str) -> dict[str, Any]:
        return self._get(container_id, fields="status_code,status")

    def wait_until_ready(self, container_id: str, timeout_seconds: int = 600) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            status = self.container_status(container_id)
            code = status.get("status_code")
            if code == "FINISHED":
                return
            if code in {"ERROR", "EXPIRED"}:
                raise PermanentError(
                    f"Instagram recusou o container {container_id}: {status}", payload=status
                )
            time.sleep(10)
        raise RetriableError(f"Container {container_id} nao terminou de processar a tempo")

    def publish(self, container_id: str) -> dict[str, Any]:
        """Post the container. NOT idempotent -- calling twice creates two posts."""
        return self._post(f"{self.user_id}/media_publish", creation_id=container_id)
