"""The publishing queue: a pure JSON store with an enforced state machine.

Instagram's ``media_publish`` is not idempotent, and the CI cron fires every 30
minutes, so an item that is picked up twice becomes a duplicate post -- the one
mistake here that cannot be undone. The safeguards live in this module:

* Only ``scheduled`` (and a ``retry`` whose backoff elapsed) is ever due, so a
  claimed item is invisible to the next run.
* Status changes go through :func:`transition`, which rejects any edge missing
  from :data:`TRANSITIONS`. Assigning ``item["status"]`` directly is a bug.
* Writes are atomic, so a crash mid-write cannot truncate the queue.

Local commands own the states up to ``scheduled``; the CI owns everything after
it. No field is written by both.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

#: Terminal states: nothing further happens to these items.
TERMINAL = frozenset({"published", "skipped", "scheduled_external"})

#: Legal status changes. Anything absent is rejected by :func:`transition`.
TRANSITIONS: dict[str, frozenset[str]] = {
    # -- local side ---------------------------------------------------
    "planned": frozenset({"prepared", "skipped"}),
    "prepared": frozenset({"hosted", "planned", "skipped"}),
    "hosted": frozenset({"scheduled", "prepared", "skipped"}),
    "scheduled": frozenset({"publishing", "planned", "skipped"}),
    # -- CI side ------------------------------------------------------
    "publishing": frozenset({"published", "retry", "failed"}),
    "retry": frozenset({"publishing", "failed", "skipped"}),
    "failed": frozenset({"scheduled", "skipped"}),
    # -- terminal -----------------------------------------------------
    "published": frozenset(),
    "skipped": frozenset({"planned"}),
    "scheduled_external": frozenset(),
}

STATES = frozenset(TRANSITIONS)

#: Meta's documented ceiling is 100 published posts per rolling 24h per account
#: (raised from 25; the carousel section of the same page still says 50, so the
#: real number is not something to ride the edge of). This is deliberately kept
#: far below it: at 2 posts/day it can only ever fire as a runaway-loop brake,
#: and the real quota is checked against ``content_publishing_limit`` anyway.
#: The reserve leaves room for anything posted by hand from the phone, which our
#: log cannot see.
DAILY_PUBLISH_LIMIT = 25
DAILY_PUBLISH_RESERVE = 2

#: Backoff before each retry, indexed by attempt number. Aligned to the 30-minute
#: cron so the first retry lands on the very next run.
RETRY_BACKOFF = (timedelta(minutes=30), timedelta(hours=2), timedelta(hours=6))
MAX_ATTEMPTS = len(RETRY_BACKOFF)


class QueueError(RuntimeError):
    """The queue was asked to do something that would corrupt its state."""


class IllegalTransition(QueueError):
    def __init__(self, item_id: str, current: str, target: str) -> None:
        allowed = ", ".join(sorted(TRANSITIONS.get(current, ()))) or "nenhum"
        super().__init__(
            f"Item {item_id}: transicao ilegal {current!r} -> {target!r} "
            f"(permitidos a partir de {current!r}: {allowed})"
        )
        self.item_id = item_id
        self.current = current
        self.target = target


def now() -> datetime:
    return datetime.now(UTC)


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    parsed = datetime.fromisoformat(stamp)
    if parsed.tzinfo is None:
        # A naive timestamp would compare against an aware "now" and raise. Treat
        # it as UTC rather than guessing the writer's local zone.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def load_queue(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": SCHEMA_VERSION, "items": []}
    queue = json.loads(path.read_text(encoding="utf-8"))
    version = queue.get("version")
    if version is None:
        raise QueueError(
            f"{path} esta no schema v1. Rode 'lukasmax migrate-queue' antes de continuar."
        )
    if version != SCHEMA_VERSION:
        raise QueueError(f"{path}: schema v{version} desconhecido (esperado v{SCHEMA_VERSION})")
    queue.setdefault("items", [])
    return queue


def save_queue(queue: dict[str, Any], path: Path) -> None:
    """Write atomically so an interrupted run never leaves a truncated queue."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(queue, ensure_ascii=False, indent=2) + "\n"
    # delete=False so the file survives close() and can be renamed into place;
    # it is closed by the `with` below and removed by the except branch.
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    )
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def find_item(queue: dict[str, Any], item_id: str) -> dict[str, Any]:
    for item in queue["items"]:
        if item.get("id") == item_id:
            return item
    raise QueueError(f"Item {item_id!r} nao existe na fila")


def counts_by_status(queue: dict[str, Any]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for item in queue["items"]:
        status = str(item.get("status") or "?")
        tally[status] = tally.get(status, 0) + 1
    return dict(sorted(tally.items()))


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def transition(
    item: dict[str, Any],
    target: str,
    *,
    by: str,
    note: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Move ``item`` to ``target``, recording who did it and why.

    ``by`` is ``"local"`` or ``"ci"``; it lands in the item's history so a
    surprising state change can be traced to the side that caused it.
    """
    current = str(item.get("status") or "")
    if target not in STATES:
        raise QueueError(f"Estado desconhecido: {target!r}")
    if target not in TRANSITIONS.get(current, frozenset()):
        raise IllegalTransition(str(item.get("id")), current, target)
    item["status"] = target
    item.update(fields)
    item.setdefault("history", []).append(
        {
            "at": now().isoformat(),
            "from": current,
            "to": target,
            "by": by,
            **({"note": note} if note else {}),
        }
    )
    return item


def find_due(queue: dict[str, Any], moment: datetime | None = None) -> list[dict[str, Any]]:
    """Items eligible to publish right now, earliest first.

    ``scheduled_at`` is a floor, not an exact time -- GitHub's cron is
    best-effort and routinely fires late, so anything already due stays due.
    """
    moment = moment or now()
    due = []
    for item in queue["items"]:
        status = item.get("status")
        if status not in {"scheduled", "retry"}:
            continue
        scheduled_at = _parse(item.get("scheduled_at"))
        if scheduled_at is None or scheduled_at > moment:
            continue
        next_attempt = _parse(item.get("next_attempt_at"))
        if next_attempt is not None and next_attempt > moment:
            continue
        due.append(item)
    due.sort(key=lambda entry: _parse(entry.get("scheduled_at")) or moment)
    return due


def schedule_retry(item: dict[str, Any], error: str, *, by: str = "ci") -> dict[str, Any]:
    """Record a transient failure and back off, or give up after MAX_ATTEMPTS."""
    attempts = int(item.get("attempts") or 0) + 1
    if attempts >= MAX_ATTEMPTS:
        return transition(
            item,
            "failed",
            by=by,
            note=f"tentativas esgotadas ({attempts}): {error}",
            attempts=attempts,
            last_error=error,
            next_attempt_at=None,
        )
    return transition(
        item,
        "retry",
        by=by,
        note=f"tentativa {attempts} falhou: {error}",
        attempts=attempts,
        last_error=error,
        next_attempt_at=(now() + RETRY_BACKOFF[attempts - 1]).isoformat(),
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def append_log(path: Path, entry: dict[str, Any]) -> None:
    """Append one line to the publish log. Append-only, never rewritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": now().isoformat(), **entry}, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            # A half-written line from a killed run should not blind the counter.
            continue
    return entries


def published_last_24h(path: Path, moment: datetime | None = None) -> int:
    moment = moment or now()
    cutoff = moment - timedelta(hours=24)
    total = 0
    for entry in read_log(path):
        if entry.get("outcome") != "published":
            continue
        stamp = _parse(entry.get("at"))
        if stamp is not None and stamp >= cutoff:
            total += 1
    return total


def daily_budget_left(path: Path, moment: datetime | None = None) -> int:
    """How many posts we may still publish before the local guard trips."""
    ceiling = DAILY_PUBLISH_LIMIT - DAILY_PUBLISH_RESERVE
    return max(0, ceiling - published_last_24h(path, moment))


# ---------------------------------------------------------------------------
# Item construction and migration
# ---------------------------------------------------------------------------


def make_item_id(tiktok_id: str, scheduled_at: str) -> str:
    stamp = scheduled_at.replace("-", "").replace(":", "")[:15]
    return f"q_{stamp}_{tiktok_id}"


def new_item(
    *,
    tiktok_id: str,
    source_url: str,
    scheduled_at: str,
    scheduled_at_utc: str,
    slot_id: str,
    rank: int | None = None,
) -> dict[str, Any]:
    return {
        "id": make_item_id(tiktok_id, scheduled_at),
        "tiktok_id": tiktok_id,
        "source_url": source_url,
        "status": "planned",
        "scheduled_at": scheduled_at,
        "scheduled_at_utc": scheduled_at_utc,
        "slot_id": slot_id,
        "rank": rank,
        "media": None,
        "caption": None,
        "caption_fingerprint": None,
        "attempts": 0,
        "max_attempts": MAX_ATTEMPTS,
        "next_attempt_at": None,
        "last_error": None,
        "container_id": None,
        "container_created_at": None,
        "claimed_at": None,
        "claimed_by_run": None,
        "instagram_media_id": None,
        "permalink": None,
        "published_at": None,
        "history": [{"at": now().isoformat(), "from": "", "to": "planned", "by": "local"}],
    }


def occupied_times(queue: dict[str, Any]) -> list[datetime]:
    """Slots already taken, so the planner never double-books one."""
    taken = []
    for item in queue["items"]:
        if item.get("status") == "skipped":
            continue
        stamp = _parse(item.get("scheduled_at"))
        if stamp is not None:
            taken.append(stamp)
    return taken


def migrate_v1(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a v1 queue to v2, preserving what the pilot already recorded.

    The pilot was scheduled by hand in Meta Business Suite, so it keeps its
    ``scheduled_external`` status and stays terminal -- the engine must never
    republish it.
    """
    items = []
    for index, old in enumerate(raw.get("items") or []):
        tiktok_id = str(old.get("tiktok_id") or "")
        scheduled_at = str(old.get("scheduled_at") or "")
        status = str(old.get("status") or "planned")
        if status == "ready":
            status = "scheduled"
        if status not in STATES:
            raise QueueError(f"Item {tiktok_id}: status v1 desconhecido {status!r}")
        item = new_item(
            tiktok_id=tiktok_id,
            source_url=str(old.get("source_url") or ""),
            scheduled_at=scheduled_at,
            scheduled_at_utc=(_parse(scheduled_at) or now()).astimezone(UTC).isoformat(),
            slot_id="legacy",
            rank=index,
        )
        item["status"] = status
        item["caption"] = old.get("caption")
        for key in ("notes", "scheduled_via", "instagram_post_id", "copyright_check"):
            if old.get(key) is not None:
                item[key] = old[key]
        if old.get("instagram_post_id"):
            item["instagram_media_id"] = str(old["instagram_post_id"])
        item["history"] = [
            {
                "at": now().isoformat(),
                "from": "",
                "to": status,
                "by": "local",
                "note": "migrado do schema v1",
            }
        ]
        items.append(item)
    return {"version": SCHEMA_VERSION, "items": items}


def iter_publishable(items: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    """Items the engine may still act on -- everything outside a terminal state."""
    for item in items:
        if item.get("status") not in TERMINAL:
            yield item
