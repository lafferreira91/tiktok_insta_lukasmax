"""Decide when each Reel goes out.

Phase 1 is a heuristic pool of slots, because the account has zero posts and
therefore no Insights data to learn from. Phase 2 (``tune-slots``) reweights
these same slots from real performance.

Three details matter more than the exact hours:

* **Rotation** stops every day collapsing onto the single highest-weight slot.
* **Deterministic jitter** keeps posts off the exact minute without making the
  planner unreproducible.
* **An exploration reserve** occasionally picks the least-sampled slot, so
  phase 2 has data about hours the heuristic never favoured. Without it the
  engine would only ever learn about the three slots it started with.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TIMEZONE = "America/Sao_Paulo"

#: Horarios medidos no proprio Instagram dele, nao herdados do TikTok.
#:
#: Os 7 primeiros posts (13/08/2026) mostraram algo que o historico do TikTok nao
#: previa: a faixa 12h-14h rende **~11x menos** que a partir das 17h -- mediana de
#: 1.460 views contra 15.738. E nao e qualidade de video: os dois grupos tem
#: ranking e views de TikTok praticamente identicos (mediana 762k para os do dia,
#: 722k para os da noite), e a serie alterna alto-baixo em vez de so subir, o que
#: descarta crescimento da conta como explicacao.
#:
#: Isso converge com a curva de ``online_followers`` da API, cujo pico cai entre
#: 19h e 22h -- duas fontes independentes apontando para o mesmo lugar.
#:
#: O slot do almoco saiu. O da manha ficou: ele **ainda nao tem nenhuma amostra**,
#: e remover por suposicao seria repetir o erro que o almoco acabou de expor.
DEFAULT_SLOTS: list[dict[str, Any]] = [
    {
        "id": "wd-morning",
        "weekdays": [0, 1, 2, 3, 4],
        "time": "09:15",
        "weight": 0.90,
        "samples": 0,
        "rationale": "sem amostra ainda; fica para ser medido antes de qualquer corte",
    },
    {
        "id": "wd-afternoon",
        "weekdays": [0, 1, 2, 3, 4],
        # 16:45 e nao 17:15 para abrir 270 minutos ate as 21:15. Com 17:15 a
        # distancia era de exatamente 240 -- o minimo -- e o jitter derrubava o
        # par noturno, empurrando o segundo post do dia para a manha.
        "time": "16:45",
        "weight": 1.20,
        "samples": 1,
        "rationale": "16.560 views no unico teste, o melhor resultado ate agora",
    },
    {
        "id": "wd-commute",
        "weekdays": [0, 1, 2, 3, 4],
        "time": "18:45",
        "weight": 1.20,
        "samples": 1,
        "rationale": "15.738 views; junto com a tarde forma a faixa que rende",
    },
    {
        "id": "wd-night",
        "weekdays": [0, 1, 2, 3, 4],
        "time": "21:15",
        "weight": 1.00,
        "samples": 0,
        "rationale": "pico de seguidores online pela API; entra para ser testado",
    },
    {
        "id": "we-late-am",
        "weekdays": [5, 6],
        "time": "10:30",
        "weight": 0.85,
        "samples": 1,
        "rationale": "497 views no unico teste, o pior de todos",
    },
    {
        "id": "we-afternoon",
        "weekdays": [5, 6],
        "time": "16:15",
        "weight": 1.10,
        "samples": 0,
        "rationale": "mesma faixa da tarde que lidera nos dias uteis",
    },
    {
        "id": "we-evening",
        "weekdays": [5, 6],
        "time": "18:30",
        "weight": 1.20,
        "samples": 2,
        "rationale": "4.988 e 1.778 views; melhor que qualquer horario diurno",
    },
    {
        "id": "we-night",
        "weekdays": [5, 6],
        "time": "21:00",
        "weight": 1.00,
        "samples": 0,
        "rationale": "mesmo teste noturno dos dias uteis, no fim de semana",
    },
]

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "source": "heuristic",
    "timezone": TIMEZONE,
    "posts_per_day": 2,
    "min_gap_minutes": 240,
    "jitter_minutes": 20,
    "explore_every": 7,
    "pool": DEFAULT_SLOTS,
}


@dataclass(frozen=True)
class PlannedSlot:
    slot_id: str
    local: datetime

    @property
    def scheduled_at(self) -> str:
        return self.local.isoformat()

    @property
    def scheduled_at_utc(self) -> str:
        return self.local.astimezone(ZoneInfo("UTC")).isoformat()


class SchedulingError(RuntimeError):
    pass


def load_slots(path: Path) -> dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    config = json.loads(path.read_text(encoding="utf-8"))
    config.setdefault("timezone", TIMEZONE)
    # setdefault, not "or": a pool the operator emptied on purpose must reach
    # plan_slots and raise, not be silently replaced by the defaults.
    config.setdefault("pool", DEFAULT_SLOTS)
    return config


def save_slots(config: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _jitter(day: date, slot_id: str, spread: int) -> int:
    """Reproducible offset in ``[-spread, +spread]`` minutes.

    Derived from the date and slot so re-running the planner yields the same
    plan -- a random offset would make every dry run disagree with the real one.
    """
    if spread <= 0:
        return 0
    digest = hashlib.sha256(f"{day.isoformat()}:{slot_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % (2 * spread + 1) - spread


def _too_close(candidate: datetime, taken: Iterable[datetime], gap: timedelta) -> bool:
    return any(abs(candidate - other) < gap for other in taken)


def plan_slots(
    start: date,
    days: int,
    config: dict[str, Any],
    occupied: Iterable[datetime] = (),
    *,
    per_day: int | None = None,
    not_before: datetime | None = None,
) -> list[PlannedSlot]:
    """Lay out ``per_day`` posting times per day for ``days`` days.

    Slots earlier than ``not_before`` (default: now) are skipped. Planning a
    window that starts today would otherwise fill the morning slots with times
    that already passed, and the next cron tick would fire all of them at once.
    """
    tz = ZoneInfo(config.get("timezone", TIMEZONE))
    floor = (not_before or datetime.now(tz)).astimezone(tz)
    per_day = per_day or int(config.get("posts_per_day", 2))
    gap = timedelta(minutes=int(config.get("min_gap_minutes", 240)))
    spread = int(config.get("jitter_minutes", 20))
    explore_every = int(config.get("explore_every", 7) or 0)
    # get(), not `or`: the latter treats a deliberately emptied pool as "not
    # set" and falls back to the defaults, scheduling posts at exactly the hours
    # the operator had just removed.
    pool = list(config.get("pool", DEFAULT_SLOTS))
    if not pool:
        raise SchedulingError("O pool de horarios esta vazio")

    taken = [moment.astimezone(tz) for moment in occupied]
    planned: list[PlannedSlot] = []
    placed = 0

    for offset in range(days):
        day = start + timedelta(days=offset)
        candidates = [slot for slot in pool if day.weekday() in slot.get("weekdays", [])]
        if not candidates:
            continue

        ranked = deque(sorted(candidates, key=lambda slot: -float(slot.get("weight", 1.0))))
        # Rotating by the day index is what keeps the best slot from winning
        # every single day while still letting it win most days.
        ranked.rotate(-(offset % len(ranked)))
        ordered = list(ranked)

        for _ in range(per_day):
            slot = _pick(ordered, candidates, placed, explore_every)
            if slot is None:
                break
            chosen = _materialize(slot, day, tz, spread, taken, gap, ordered, floor)
            if chosen is None:
                break
            planned.append(chosen)
            taken.append(chosen.local)
            ordered = [entry for entry in ordered if entry["id"] != chosen.slot_id]
            placed += 1

    # Dentro de um dia os slots sao escolhidos por peso, nao por relogio, entao
    # 'planned' pode sair fora de ordem cronologica. Quem consome esta lista
    # (reschedule, plan_queue) casa o video melhor ranqueado com o primeiro
    # horario da lista -- sem esta ordenacao o melhor video podia cair no
    # horario mais tarde do dia por acaso.
    planned.sort(key=lambda entry: entry.local)
    return planned


def _pick(
    ordered: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    placed: int,
    explore_every: int,
) -> dict[str, Any] | None:
    if not ordered:
        return None
    if explore_every and placed and placed % explore_every == 0:
        # Exploration turn: give the least-observed slot a chance, so phase 2
        # has evidence about hours the prior never favoured.
        unexplored = sorted(ordered, key=lambda slot: (int(slot.get("samples", 0)), slot["id"]))
        return unexplored[0]
    return ordered[0]


def _materialize(
    slot: dict[str, Any],
    day: date,
    tz: ZoneInfo,
    spread: int,
    taken: list[datetime],
    gap: timedelta,
    ordered: list[dict[str, Any]],
    floor: datetime,
) -> PlannedSlot | None:
    """Turn a slot into a concrete datetime, or fall through to the next slot."""
    for option in [slot] + [entry for entry in ordered if entry["id"] != slot["id"]]:
        hour, minute = (int(part) for part in str(option["time"]).split(":"))
        base = datetime.combine(day, time(hour, minute), tzinfo=tz)
        moment = base + timedelta(minutes=_jitter(day, option["id"], spread))
        if moment <= floor:
            continue  # already past: the cron would fire it on the next tick
        if not _too_close(moment, taken, gap):
            return PlannedSlot(slot_id=option["id"], local=moment)
    return None


def describe(planned: Iterable[PlannedSlot]) -> list[dict[str, str]]:
    return [{"slot_id": slot.slot_id, "scheduled_at": slot.scheduled_at} for slot in planned]


# ---------------------------------------------------------------------------
# Phase 2: reweighting from real Insights
# ---------------------------------------------------------------------------

#: Shrinkage strength. With only a handful of posts per slot a raw mean is
#: noise, so each slot is pulled toward the global average until it has earned
#: its own estimate.
SHRINKAGE_K = 5


def tune_weights(config: dict[str, Any], performance: dict[str, list[float]]) -> dict[str, Any]:
    """Reweight slots from observed performance, with Bayesian shrinkage.

    ``performance`` maps slot id to per-post scores (interactions / reach). A
    slot with no observations keeps its prior and is never dropped -- removing
    it would guarantee it never gets tested again.
    """
    observed = [value for values in performance.values() for value in values]
    if not observed:
        return config
    global_mean = sum(observed) / len(observed)
    if global_mean <= 0:
        return config

    tuned = json.loads(json.dumps(config))
    for slot in tuned["pool"]:
        values = performance.get(slot["id"]) or []
        samples = len(values)
        slot["samples"] = samples
        if not samples:
            continue
        slot_mean = sum(values) / samples
        weight = SHRINKAGE_K / (samples + SHRINKAGE_K)
        blended = global_mean * weight + slot_mean * (1 - weight)
        slot["weight"] = round(blended / global_mean, 4)
    tuned["source"] = "data-driven"
    tuned["updated_at"] = datetime.now(ZoneInfo(config.get("timezone", TIMEZONE))).isoformat()
    return tuned
