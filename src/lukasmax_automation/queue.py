from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .instagram import InstagramPublisher
from .media import normalize_for_instagram
from .tiktok import download_without_watermark


def publish_due(queue_path: Path, media_dir: Path) -> list[str]:
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    if os.environ.get("PUBLISH_ENABLED", "false").lower() != "true":
        return []
    user_id = os.environ["INSTAGRAM_USER_ID"]
    token = os.environ["INSTAGRAM_ACCESS_TOKEN"]
    api_version = os.environ.get("INSTAGRAM_API_VERSION", "v25.0")
    publisher = InstagramPublisher(user_id, token, api_version)
    now = datetime.now().astimezone()
    published = []
    for item in queue["items"]:
        if item["status"] != "ready":
            continue
        if datetime.fromisoformat(item["scheduled_at"]) > now:
            continue
        output = str(media_dir / f"{item['tiktok_id']}.%(ext)s")
        source_video = download_without_watermark(item["source_url"], output)
        ready_video = media_dir / "ready" / f"{item['tiktok_id']}.mp4"
        normalize_for_instagram(source_video, ready_video)
        result = publisher.publish_reel(ready_video, item["caption"])
        item.update(status="published", instagram_media_id=result["id"], published_at=now.isoformat())
        published.append(result["id"])
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return published
