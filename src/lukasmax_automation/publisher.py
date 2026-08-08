"""The CI side of the pipeline: claim a due item, post it, record what happened.

This module deliberately imports neither :mod:`.tiktok` nor :mod:`.media`. The
video was downloaded, normalized and hosted on the Mac; the runner only hands
Meta a URL. That keeps yt-dlp, ffmpeg and PyAV out of the publish job entirely,
and removes the old behaviour of re-downloading from TikTok at publish time --
a network dependency on the least reliable part of the whole system.

The duplicate-post guard has three layers:

1. Only ``scheduled`` items (and elapsed ``retry`` items) are due.
2. The claim is written and pushed *before* the container is created, so a
   second run sees ``publishing`` and skips.
3. :func:`reconcile` resolves anything stuck in ``publishing`` by asking Meta
   what actually happened, because ``media_publish`` is not idempotent and a
   blind retry would double-post.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from . import instagram
from . import queue as queue_mod
from .instagram import InstagramError, InstagramPublisher, PermanentError, RetriableError
from .paths import Paths

#: How long a claim may sit in ``publishing`` before reconcile investigates it.
#: Longer than a normal publish (container processing plus upload), short enough
#: that a crashed run is picked up on the next cron tick.
STALE_CLAIM = timedelta(minutes=45)


class Publisher(Protocol):
    """The slice of :class:`~.instagram.InstagramPublisher` this module uses."""

    def create_container_from_url(self, video_url: str, caption: str, **kwargs: Any) -> dict: ...
    def wait_until_ready(self, container_id: str, **kwargs: Any) -> None: ...
    def container_status(self, container_id: str) -> dict: ...
    def publish(self, container_id: str) -> dict: ...
    def content_publishing_limit(self) -> dict: ...
    def list_recent_media(self, limit: int = 10) -> list[dict]: ...
    def permalink(self, media_id: str) -> str | None: ...


def publishing_enabled() -> bool:
    return os.environ.get("PUBLISH_ENABLED", "false").strip().lower() == "true"


def build_publisher() -> InstagramPublisher:
    missing = [
        name for name in ("INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN") if not os.environ.get(name)
    ]
    if missing:
        raise RuntimeError(f"Variaveis ausentes: {', '.join(missing)}")
    return InstagramPublisher(
        os.environ["INSTAGRAM_USER_ID"],
        os.environ["INSTAGRAM_ACCESS_TOKEN"],
        os.environ.get("INSTAGRAM_API_VERSION", instagram.DEFAULT_API_VERSION),
    )


def run_id() -> str:
    return os.environ.get("GITHUB_RUN_ID") or f"local-{os.getpid()}"


def _quota_blocked(publisher: Publisher, paths: Paths) -> str | None:
    """Both quota guards. Returns a reason to stop, or None to proceed."""
    local_left = queue_mod.daily_budget_left(paths.publish_log)
    if local_left <= 0:
        return f"limite local de 24h atingido ({queue_mod.DAILY_PUBLISH_LIMIT} posts)"
    try:
        limit = publisher.content_publishing_limit()
    except InstagramError:
        # Meta's quota endpoint is advisory; the local counter already guards us.
        return None
    used = int(limit.get("quota_usage") or 0)
    total = int((limit.get("config") or {}).get("quota_total") or queue_mod.DAILY_PUBLISH_LIMIT)
    if used >= total:
        return f"quota da Meta esgotada ({used}/{total})"
    return None


def claim(
    item: dict[str, Any],
    queue: dict[str, Any],
    paths: Paths,
    *,
    persist: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Mark the item as ours and persist immediately.

    ``persist`` is what makes this a lock: in CI it commits and pushes, and a
    rejected push aborts the run before anything is posted. Two runs can never
    both see the item as ``scheduled``.
    """
    queue_mod.transition(
        item,
        "publishing",
        by="ci",
        note=f"claim do run {run_id()}",
        claimed_at=queue_mod.now().isoformat(),
        claimed_by_run=run_id(),
    )
    persist(queue)
    return item


def publish_item(item: dict[str, Any], publisher: Publisher, paths: Paths) -> dict[str, Any]:
    """Create the container, wait for processing, publish. Records every outcome."""
    media = item.get("media") or {}
    video_url = media.get("asset_url")
    if not video_url:
        raise PermanentError(f"Item {item['id']} nao tem asset_url; rode 'lukasmax host-media'")

    container_id = item.get("container_id")
    if not container_id:
        container = publisher.create_container_from_url(video_url, item.get("caption") or "")
        container_id = str(container["id"])
        item["container_id"] = container_id
        item["container_created_at"] = queue_mod.now().isoformat()

    publisher.wait_until_ready(container_id)
    result = publisher.publish(container_id)
    media_id = str(result["id"])

    queue_mod.transition(
        item,
        "published",
        by="ci",
        instagram_media_id=media_id,
        permalink=publisher.permalink(media_id),
        published_at=queue_mod.now().isoformat(),
        last_error=None,
        next_attempt_at=None,
    )
    queue_mod.append_log(
        paths.publish_log,
        {
            "item_id": item["id"],
            "tiktok_id": item.get("tiktok_id"),
            "container_id": container_id,
            "media_id": media_id,
            "run_id": run_id(),
            "outcome": "published",
        },
    )
    return item


def reconcile(
    queue: dict[str, Any],
    publisher: Publisher,
    paths: Paths,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Resolve items left in ``publishing`` by a crashed run.

    ``media_publish`` has no idempotency key, so we cannot simply retry. Instead
    we ask Meta what state the container reached, and for an old claim we cross
    check the account's recent media: if something was posted after the claim,
    the crash happened *after* a successful publish and retrying would duplicate
    it.

    ``dry_run`` stops before every mutation. This matters more than it looks: a
    stranded ``FINISHED`` container is resolved by calling ``media_publish``, so
    without this flag ``publish-due --dry-run`` posts for real -- the one thing
    its name promises it will not do.
    """
    resolved = []
    for item in list(queue["items"]):
        if item.get("status") != "publishing":
            continue
        if dry_run:
            resolved.append(item)
            continue
        container_id = item.get("container_id")
        claimed_at = item.get("claimed_at")
        claimed = datetime.fromisoformat(claimed_at) if claimed_at else None
        if claimed is not None and claimed.tzinfo is None:
            claimed = claimed.replace(tzinfo=UTC)

        if not container_id:
            # Crashed before the container existed: nothing was posted.
            queue_mod.schedule_retry(item, "claim sem container; nada foi postado")
            resolved.append(item)
            continue

        already = _already_published(item, publisher, claimed)
        if already:
            queue_mod.transition(
                item,
                "published",
                by="ci",
                note="reconciliado: a publicacao tinha dado certo antes do crash",
                instagram_media_id=already["id"],
                permalink=already.get("permalink"),
                published_at=already.get("timestamp") or queue_mod.now().isoformat(),
            )
            queue_mod.append_log(
                paths.publish_log,
                {
                    "item_id": item["id"],
                    "container_id": container_id,
                    "media_id": already["id"],
                    "run_id": run_id(),
                    "outcome": "published",
                    "note": "reconciled",
                },
            )
            resolved.append(item)
            continue

        try:
            status = publisher.container_status(container_id).get("status_code")
        except InstagramError as error:
            queue_mod.schedule_retry(item, f"nao consegui ler o container: {error}")
            resolved.append(item)
            continue

        if status == "FINISHED":
            try:
                publish_item(item, publisher, paths)
            except RetriableError as error:
                queue_mod.schedule_retry(item, str(error))
            except (PermanentError, KeyError) as error:
                queue_mod.transition(
                    item, "failed", by="ci", note=str(error), last_error=str(error)
                )
            resolved.append(item)
        elif status in {"ERROR", "EXPIRED"}:
            item["container_id"] = None  # a new attempt needs a fresh container
            queue_mod.schedule_retry(item, f"container {status.lower()}")
            resolved.append(item)
        elif claimed is not None and queue_mod.now() - claimed > STALE_CLAIM:
            item["container_id"] = None
            queue_mod.schedule_retry(item, "claim preso sem conclusao")
            resolved.append(item)
        # Still IN_PROGRESS and recent: leave it alone, the next run checks again.
    return resolved


def _already_published(
    item: dict[str, Any], publisher: Publisher, claimed: datetime | None
) -> dict[str, Any] | None:
    """Look for media posted after the claim -- evidence the publish succeeded."""
    if claimed is None or queue_mod.now() - claimed <= STALE_CLAIM:
        return None
    try:
        recent = publisher.list_recent_media(limit=10)
    except InstagramError:
        return None
    for media in recent:
        stamp = media.get("timestamp")
        if not stamp:
            continue
        try:
            posted = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=UTC)
        if posted >= claimed:
            return media
    return None


def publish_due(
    queue_path: Path,
    paths: Paths,
    *,
    publisher: Publisher | None = None,
    max_per_run: int = 1,
    dry_run: bool = False,
    persist: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Publish at most ``max_per_run`` due items. The CI entry point.

    Defaults to one item per run so any bug can only damage a single post.
    """
    queue = queue_mod.load_queue(queue_path)
    persist = persist or (lambda data: queue_mod.save_queue(data, queue_path))

    if not publishing_enabled() and not dry_run:
        return {"enabled": False, "published": [], "skipped": "PUBLISH_ENABLED nao esta true"}

    outcome: dict[str, Any] = {"enabled": True, "published": [], "failed": [], "reconciled": []}

    # Um dry run sem credenciais ainda responde "o que voce publicaria agora?",
    # porque a fila sozinha responde isso -- e sem essa degradacao era
    # impossivel exercitar este caminho no CI, que e onde ele roda.
    #
    # A degradacao e o ultimo recurso, nao o caminho normal: com token na mao o
    # dry run continua reconciliando e conferindo a quota, que e o que torna a
    # previsao fiel. Condicionar em "publisher is None" degradava toda execucao
    # vinda do CLI, inclusive as que tinham credencial.
    if publisher is None:
        try:
            publisher = build_publisher()
        except RuntimeError:
            if not dry_run:
                raise
            outcome["due"] = [item["id"] for item in queue_mod.find_due(queue)[:max_per_run]]
            outcome["dry_run"] = True
            outcome["nota"] = "sem credenciais: reconciliacao e quota nao foram consultadas"
            return outcome

    reconciled = reconcile(queue, publisher, paths, dry_run=dry_run)
    if reconciled:
        outcome["reconciled"] = [item["id"] for item in reconciled]
        if not dry_run:
            persist(queue)

    blocked = _quota_blocked(publisher, paths)
    if blocked:
        outcome["skipped"] = blocked
        return outcome

    due = queue_mod.find_due(queue)[:max_per_run]
    outcome["due"] = [item["id"] for item in due]
    if dry_run:
        outcome["dry_run"] = True
        return outcome

    for item in due:
        claim(item, queue, paths, persist=persist)
        try:
            publish_item(item, publisher, paths)
            outcome["published"].append(item["id"])
        except RetriableError as error:
            queue_mod.schedule_retry(item, str(error))
            queue_mod.append_log(
                paths.publish_log,
                {
                    "item_id": item["id"],
                    "run_id": run_id(),
                    "outcome": "retry",
                    "error": str(error),
                },
            )
            outcome["failed"].append({"id": item["id"], "error": str(error), "retriable": True})
        except Exception as error:  # permanent, or a bug -- either way stop trying
            queue_mod.transition(item, "failed", by="ci", note=str(error), last_error=str(error))
            queue_mod.append_log(
                paths.publish_log,
                {
                    "item_id": item["id"],
                    "run_id": run_id(),
                    "outcome": "failed",
                    "error": str(error),
                },
            )
            outcome["failed"].append({"id": item["id"], "error": str(error), "retriable": False})
        finally:
            persist(queue)

    return outcome
