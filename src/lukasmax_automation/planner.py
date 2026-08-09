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
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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


#: Estados que ainda podem ser re-datados. Publicado e 'publishing' ficam fora
#: pelos mesmos motivos de sempre: um ja foi ao ar, o outro tem container aberto.
RESCHEDULABLE = frozenset({"planned", "prepared", "hosted", "scheduled"})


def reschedule(
    queue: dict[str, Any],
    config: dict[str, Any],
    *,
    per_day: int = 2,
    start: date | None = None,
    not_before: datetime | None = None,
) -> dict[str, Any]:
    """Redistribui os itens pendentes sobre o pool de horarios atual.

    Trocar o pool em ``data/slots.json`` nao mexe em quem ja tem horario
    gravado, entao mudar de ideia sobre os horarios precisa deste passo. A ordem
    dos videos e preservada: quem estava primeiro continua primeiro, so o
    relogio muda.
    """
    pendentes = [item for item in queue["items"] if item.get("status") in RESCHEDULABLE]
    if not pendentes:
        return {"redatados": [], "nota": "nada pendente para redatar"}

    pendentes.sort(key=lambda item: str(item.get("scheduled_at") or ""))
    intocaveis = [
        item["scheduled_at"]
        for item in queue["items"]
        if item.get("status") not in RESCHEDULABLE and item.get("scheduled_at")
    ]

    piso = not_before or queue_mod.now()
    inicio = start or piso.astimezone(ZoneInfo(config.get("timezone", "America/Sao_Paulo"))).date()
    dias = max(1, -(-len(pendentes) // max(per_day, 1)) + 2)

    slots = scheduling.plan_slots(
        inicio,
        dias,
        config,
        occupied=[datetime.fromisoformat(t) for t in intocaveis],
        per_day=per_day,
        not_before=piso,
    )
    if len(slots) < len(pendentes):
        raise scheduling.SchedulingError(
            f"{len(slots)} horarios para {len(pendentes)} itens; aumente os dias ou o per_day"
        )

    redatados = []
    for item, slot in zip(pendentes, slots, strict=False):
        antes = item.get("scheduled_at")
        item["scheduled_at"] = slot.scheduled_at
        item["scheduled_at_utc"] = slot.scheduled_at_utc
        item["slot_id"] = slot.slot_id
        redatados.append({"id": item["tiktok_id"], "antes": antes, "depois": slot.scheduled_at})

    return {
        "redatados": redatados,
        "primeiro": slots[0].scheduled_at,
        "ultimo": slots[len(pendentes) - 1].scheduled_at,
    }


#: Estados em que ainda da para decidir se o post e teste. Publicado e
#: 'publishing' ficam de fora: o primeiro ja foi ao ar, o segundo tem container
#: aberto -- e o container e onde 'trial_params' viaja.
TRIALABLE = frozenset({"planned", "prepared", "hosted", "scheduled", "retry"})

#: MANUAL, nao SS_PERFORMANCE: nada sobe sozinho para o perfil. Se um teste
#: explodir, a graduacao e um toque no app -- a decisao continua sendo humana.
TRIAL_STRATEGY = "MANUAL"

#: Esta conta NAO pode publicar reels de teste. Testado ao vivo em 09/08/2026:
#: o mesmo video, na mesma chamada, com ``trial_params`` devolve
#: ``400: Application does not have permission for this action`` e sem ele o
#: container e criado normalmente. Nao e bug do codigo -- e uma permissao que o
#: app nao tem, e a Meta nao documenta como obte-la.
#:
#: O codigo fica: se a permissao aparecer, basta ``--force``. Ate la o comando
#: recusa, porque marcar um item aqui significa um post que falha em producao.
TRIAL_PERMISSION_MISSING = (
    "A conta nao tem permissao para reels de teste: a Meta recusa 'trial_params' "
    "com 400 'Application does not have permission for this action'. "
    "Confirme no painel do app e use --force quando a permissao existir."
)


def mark_trials(
    queue: dict[str, Any],
    *,
    clear: bool = False,
    limit_days: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Marca um dos dois posts de cada dia como reel de teste.

    Reel de teste so alcanca **nao seguidores**. Com 170 seguidores, quase todo
    alcance possivel esta fora deles, entao dedicar um dos dois posts diarios a
    esse publico dobra a superficie sem duplicar nada: sao videos diferentes, e o
    consumo do acervo nao muda.

    O escolhido e o de **rank mais baixo do par** -- os dois ranks de um dia sao
    vizinhos, entao a diferenca e pequena, mas o criterio e deterministico e o
    video mais forte fica com o perfil.

    Publicar o *mesmo* video nas duas versoes seria pior: o reel normal tambem e
    distribuido para nao seguidores, entao as duas copias disputariam o mesmo
    publico com o mesmo conteudo.

    ``limit_days`` marca apenas os N primeiros dias. Existe para a liberacao em
    etapas: ``reconcile`` detecta "o run morreu depois de publicar" cruzando com
    a lista de midias recentes da conta, e **nao esta confirmado que um reel de
    teste aparece nessa lista**. Se nao aparecer, um crash na hora errada vira
    post duplicado -- a unica falha irreversivel do projeto. Por isso o primeiro
    teste vai com ``limit_days=1``.
    """
    claimed = [item for item in queue["items"] if item.get("status") == "publishing"]
    if claimed:
        raise queue_mod.QueueError(
            f"{len(claimed)} item(ns) em 'publishing'. Rode 'lukasmax reconcile' antes."
        )

    elegiveis = [item for item in queue["items"] if item.get("status") in TRIALABLE]

    if not clear and not force:
        raise queue_mod.QueueError(TRIAL_PERMISSION_MISSING)

    if clear:
        # Limpa de TODO item nao publicado, nao so dos marcaveis: um item que
        # falhou ao publicar sai de TRIALABLE mas continua com a marca, e
        # devolve-lo para a fila reintroduziria a mesma falha. Foi exatamente o
        # que aconteceu em 09/08/2026.
        limpos = [
            item["id"]
            for item in queue["items"]
            if item.get("status") not in queue_mod.TERMINAL and item.pop("trial", None) is not None
        ]
        return {"limpos": limpos, "marcados": []}

    por_dia: dict[str, list[dict[str, Any]]] = {}
    for item in elegiveis:
        dia = str(item.get("scheduled_at") or "")[:10]
        if dia:
            por_dia.setdefault(dia, []).append(item)

    dias = sorted(por_dia.items())
    if limit_days is not None:
        dias = dias[:limit_days]

    marcados: list[dict[str, Any]] = []
    for dia, itens in dias:
        if len(itens) < 2:
            # Dia com um post so: ele fica no perfil. Um dia inteiro sem nada no
            # feed seria pior que um dia sem teste.
            continue
        # rank ausente vai para o fim: sem ranking, e o candidato mais fraco.
        escolhido = max(itens, key=lambda i: (i.get("rank") is None, i.get("rank") or 0))
        escolhido["trial"] = {"graduation_strategy": TRIAL_STRATEGY}
        marcados.append({"id": escolhido["id"], "dia": dia, "rank": escolhido.get("rank")})

    # Idempotencia: um item que deixou de ser o escolhido perde a marca, senao
    # rodar duas vezes com a fila alterada acumularia testes.
    escolhidos = {m["id"] for m in marcados}
    for item in elegiveis:
        if item["id"] not in escolhidos:
            item.pop("trial", None)

    return {"marcados": marcados, "limpos": [], "dias_com_teste": len(marcados)}
