"""Turn the ranking into a scheduled queue -- the bridge that was missing.

Until now nothing connected ``tiktok_ranking.csv`` to ``queue.json``: the single
queued item was written by hand. This module walks the ranking in order, pairs
each eligible video with a planned slot, and freezes the approved caption into
the item.

Freezing matters: the CI never reads ``data/captions/``, so editing a caption
file afterwards cannot silently change a post that is already scheduled.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from . import captions as captions_mod
from . import queue as queue_mod
from . import scheduling
from .paths import Paths


@dataclass(frozen=True)
class Candidate:
    tiktok_id: str
    url: str
    rank: int
    score: float


@dataclass(frozen=True)
class Rejection:
    tiktok_id: str
    reason: str


def read_ranking(path: Path) -> list[Candidate]:
    if not path.exists():
        raise FileNotFoundError(f"Ranking ausente: {path}. Rode 'lukasmax audit-tiktok' antes.")
    candidates = []
    with path.open(newline="", encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            candidates.append(
                Candidate(
                    tiktok_id=str(row["id"]),
                    url=str(row.get("url") or ""),
                    rank=index,
                    score=float(row.get("score") or 0.0),
                )
            )
    return candidates


def eligibility(candidate: Candidate, paths: Paths, queued: set[str]) -> str | None:
    """Why this video cannot be scheduled yet, or None if it can."""
    if candidate.tiktok_id in queued:
        return "ja esta na fila"
    if not paths.ready(candidate.tiktok_id).exists():
        return "midia nao preparada (rode 'lukasmax prepare')"
    record = captions_mod.load(paths.caption(candidate.tiktok_id))
    if record is None:
        return "sem legenda (rode 'lukasmax draft-captions')"
    if record.get("status") != "approved":
        return "legenda nao aprovada (rode 'lukasmax approve-caption')"
    if not (record.get("caption") or "").strip():
        return "legenda vazia"
    return None


def interleave(candidates: list[Candidate]) -> list[Candidate]:
    """Alternate top-of-ranking with mid-ranking, to spread the strong videos out."""
    half = (len(candidates) + 1) // 2
    top, rest = candidates[:half], candidates[half:]
    mixed: list[Candidate] = []
    for index in range(half):
        mixed.append(top[index])
        if index < len(rest):
            mixed.append(rest[index])
    return mixed


def plan_queue(
    paths: Paths,
    *,
    start: date,
    days: int,
    per_day: int = 2,
    strategy: str = "front-loaded",
    queue: dict[str, Any] | None = None,
    slots_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Schedule as many eligible videos as there are slots in the window."""
    queue = queue if queue is not None else queue_mod.load_queue(paths.queue)

    claimed = [item for item in queue["items"] if item.get("status") == "publishing"]
    if claimed:
        raise queue_mod.QueueError(
            f"{len(claimed)} item(ns) em 'publishing'. "
            "Rode 'lukasmax reconcile' antes de replanejar."
        )

    config = slots_config if slots_config is not None else scheduling.load_slots(paths.slots)
    candidates = read_ranking(paths.ranking_csv)
    if strategy == "interleaved":
        candidates = interleave(candidates)
    elif strategy != "front-loaded":
        raise ValueError(f"Estrategia desconhecida: {strategy!r}")

    queued = {
        str(item.get("tiktok_id")) for item in queue["items"] if item.get("status") != "skipped"
    }

    eligible: list[Candidate] = []
    rejected: list[Rejection] = []
    for candidate in candidates:
        reason = eligibility(candidate, paths, queued)
        if reason:
            rejected.append(Rejection(candidate.tiktok_id, reason))
        else:
            eligible.append(candidate)

    planned = scheduling.plan_slots(
        start, days, config, queue_mod.occupied_times(queue), per_day=per_day
    )

    created: list[dict[str, Any]] = []
    # Truncating to the shorter side is the point: leftover slots and leftover
    # eligible videos are both reported back in the summary.
    for candidate, slot in zip(eligible, planned, strict=False):
        record = captions_mod.load(paths.caption(candidate.tiktok_id)) or {}
        item = queue_mod.new_item(
            tiktok_id=candidate.tiktok_id,
            source_url=candidate.url
            or f"https://www.tiktok.com/@_lukasmax/video/{candidate.tiktok_id}",
            scheduled_at=slot.scheduled_at,
            scheduled_at_utc=slot.scheduled_at_utc,
            slot_id=slot.slot_id,
            rank=candidate.rank,
        )
        # Copy, do not reference: a later edit to the caption file must not
        # change a post that is already on the calendar.
        item["caption"] = captions_mod.full_caption(record)
        item["caption_fingerprint"] = record.get("input_fingerprint")
        item["caption_ref"] = str(paths.caption(candidate.tiktok_id).relative_to(paths.root))
        item["alt_text"] = record.get("alt_text")
        created.append(item)

    return {
        "queue": queue,
        "created": created,
        "planned_slots": len(planned),
        "eligible": len(eligible),
        "rejected": rejected,
        "unused_slots": max(0, len(planned) - len(eligible)),
        "unscheduled_eligible": max(0, len(eligible) - len(planned)),
    }


def commit_plan(result: dict[str, Any], paths: Paths) -> int:
    """Append the planned items and advance them to ``prepared``/``hosted``.

    They start at ``planned`` for the audit trail, then move up as far as the
    facts on disk justify -- media is prepared, so ``prepared`` is always true
    here; ``hosted`` waits for ``host-media``.
    """
    queue = result["queue"]
    for item in result["created"]:
        queue_mod.transition(item, "prepared", by="local", note="midia normalizada em disco")
        queue["items"].append(item)
    queue["items"].sort(key=lambda entry: str(entry.get("scheduled_at") or ""))
    queue_mod.save_queue(queue, paths.queue)
    return len(result["created"])


def summarize_rejections(rejected: Iterable[Rejection]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for entry in rejected:
        tally[entry.reason] = tally.get(entry.reason, 0) + 1
    return dict(sorted(tally.items(), key=lambda pair: -pair[1]))


#: Estados em que a legenda ainda pode ser trocada. 'publishing' fica de fora de
#: proposito: o container ja existe na Meta com o texto antigo, entao reescrever
#: aqui so criaria divergencia entre o que a fila diz e o que foi postado.
REFRESHABLE = frozenset({"planned", "prepared", "hosted", "scheduled", "retry"})


def refresh_captions(
    queue: dict[str, Any],
    paths: Paths,
    *,
    ids: Iterable[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Recopia as legendas aprovadas para os itens que ainda nao foram ao ar.

    O congelamento existe para que editar ``data/captions/`` nunca mude sozinho
    um post agendado. Trocar a legenda de propositio precisa entao de um caminho
    explicito -- este -- e ele nunca toca em item publicado.
    """
    wanted = set(ids or [])
    trocadas: list[dict[str, Any]] = []
    ignoradas: list[dict[str, Any]] = []

    for item in queue["items"]:
        video_id = item["tiktok_id"]
        if wanted and video_id not in wanted:
            continue
        if item.get("status") not in REFRESHABLE:
            ignoradas.append({"id": video_id, "motivo": f"status {item.get('status')}"})
            continue

        record = captions_mod.load(paths.caption(video_id))
        if not record or record.get("status") != "approved":
            ignoradas.append({"id": video_id, "motivo": "legenda nao aprovada"})
            continue

        novo = captions_mod.full_caption(record)
        if not novo.strip():
            ignoradas.append({"id": video_id, "motivo": "legenda vazia"})
            continue
        if novo == item.get("caption"):
            continue

        antes = item.get("caption") or ""
        item["caption"] = novo
        item["caption_fingerprint"] = record.get("input_fingerprint")
        item["alt_text"] = record.get("alt_text")
        trocadas.append(
            {"id": video_id, "antes": antes.split("\n")[0][:60], "depois": novo.split("\n")[0][:60]}
        )

    return {"trocadas": trocadas, "ignoradas": ignoradas}
