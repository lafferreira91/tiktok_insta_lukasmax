from __future__ import annotations

import json
import mimetypes
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class InstagramPublisher:
    def __init__(self, user_id: str, access_token: str, api_version: str = "v25.0"):
        self.user_id = user_id
        self.access_token = access_token
        self.base = f"https://graph.instagram.com/{api_version}"

    def _request(self, method: str, url: str, data: dict[str, Any] | bytes | None = None, headers=None):
        headers = dict(headers or {})
        body = data
        if isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode()
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=300) as response:
            return json.loads(response.read())

    def create_container(self, caption: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self.base}/{self.user_id}/media",
            {
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": caption,
                "share_to_feed": "true",
                "access_token": self.access_token,
            },
        )

    def check_account(self) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "fields": "id,user_id,username,account_type",
                "access_token": self.access_token,
            }
        )
        return self._request("GET", f"{self.base}/{self.user_id}?{query}")

    def upload(self, upload_uri: str, video: Path) -> dict[str, Any]:
        content = video.read_bytes()
        return self._request(
            "POST",
            upload_uri,
            content,
            {
                "Authorization": f"OAuth {self.access_token}",
                "Content-Type": mimetypes.guess_type(video.name)[0] or "video/mp4",
                "offset": "0",
                "file_size": str(len(content)),
            },
        )

    def wait_until_ready(self, container_id: str, timeout_seconds: int = 600) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            query = urllib.parse.urlencode(
                {"fields": "status_code,status", "access_token": self.access_token}
            )
            status = self._request("GET", f"{self.base}/{container_id}?{query}")
            if status.get("status_code") == "FINISHED":
                return
            if status.get("status_code") in {"ERROR", "EXPIRED"}:
                raise RuntimeError(f"Instagram recusou o container: {status}")
            time.sleep(10)
        raise TimeoutError("O Instagram nao terminou de processar o Reel")

    def publish(self, container_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self.base}/{self.user_id}/media_publish",
            {"creation_id": container_id, "access_token": self.access_token},
        )

    def publish_reel(self, video: Path, caption: str) -> dict[str, Any]:
        container = self.create_container(caption)
        container_id = container["id"]
        upload_uri = container.get("uri") or (
            f"https://rupload.facebook.com/ig-api-upload/{self.base.rsplit('/', 1)[-1]}/{container_id}"
        )
        self.upload(upload_uri, video)
        self.wait_until_ready(container_id)
        return self.publish(container_id)
