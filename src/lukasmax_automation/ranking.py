from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import log1p
from typing import Any


@dataclass(frozen=True)
class RankedVideo:
    id: str
    url: str
    title: str
    views: int
    likes: int
    comments: int
    shares: int
    score: float


def _number(item: Mapping[str, Any], key: str) -> int:
    return int(item.get(key) or 0)


def rank_videos(entries: Iterable[Mapping[str, Any]]) -> list[RankedVideo]:
    """Rank videos using scale, interaction quality and share intent.

    Logarithms prevent a single viral outlier from hiding videos with unusually
    strong engagement. Shares and comments carry more weight than likes.
    """
    ranked: list[RankedVideo] = []
    for item in entries:
        views = _number(item, "view_count")
        likes = _number(item, "like_count")
        comments = _number(item, "comment_count")
        shares = _number(item, "repost_count") or _number(item, "share_count")
        if not item.get("id") or views <= 0:
            continue
        weighted_engagement = likes + (comments * 3) + (shares * 5)
        engagement_rate = weighted_engagement / views
        score = (
            (log1p(views) * 0.62)
            + (log1p(weighted_engagement) * 0.23)
            + (min(engagement_rate, 1.0) * 15 * 0.15)
        )
        url = item.get("webpage_url") or item.get("url") or ""
        ranked.append(
            RankedVideo(
                id=str(item["id"]),
                url=str(url),
                title=str(item.get("title") or item.get("description") or ""),
                views=views,
                likes=likes,
                comments=comments,
                shares=shares,
                score=round(score, 6),
            )
        )
    return sorted(ranked, key=lambda video: video.score, reverse=True)
