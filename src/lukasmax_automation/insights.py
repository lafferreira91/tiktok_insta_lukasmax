"""Como cada Reel publicado se saiu, medido sempre na mesma idade.

Este modulo fecha o circuito que estava aberto: :meth:`InstagramPublisher.media_insights`
existia sem ninguem chamar, e :func:`scheduling.tune_weights` existia sem ninguem
alimentar. O que faltava era o meio de campo -- e o meio de campo tem uma regra
que define se o dado presta ou nao.

**A comparacao e por idade, nao por data de coleta.** Um post de dois dias
comparado com um de sessenta mede idade, nao qualidade, e o numero continua
parecendo razoavel -- e por isso que o erro passa despercebido. Cada post e
medido as 24h e aos 7 dias de vida, e a idade real vai gravada na linha.

O arquivo e append-only, uma linha por ``(media_id, idade)``, na mesma convencao
do ``publish_log.jsonl``. Sobrescrever destruiria justamente a informacao de
idade, que a API nao devolve retroativamente: o que nao foi gravado as 24h nao
existe mais. De brinde, a chave ja presente no arquivo *e* o registro de "ja
coletei" -- idempotencia sem estado extra e, principalmente, sem escrever em
``queue.json``, que e disputado pelo job de publicacao.

Nada aqui chama ``transition``. ``published`` e estado terminal com transicoes
vazias: qualquer tentativa levantaria excecao.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from . import queue as queue_mod

#: As metricas que a conta responde para REELS. Conferidas ao vivo --
#: ``profile_visits`` e ``follows`` existem na API mas sao recusadas para reels.
REELS_METRICS: tuple[str, ...] = (
    "views",
    "reach",
    "likes",
    "comments",
    "shares",
    "saved",
    "total_interactions",
    "ig_reels_avg_watch_time",
)

#: Em que idades cada post e medido.
SNAPSHOT_AGES: dict[str, timedelta] = {
    "h24": timedelta(hours=24),
    "d7": timedelta(days=7),
}

#: Quanto uma coleta pode atrasar e ainda contar como daquela idade. O cron roda
#: uma vez por dia, entao nunca acerta a hora exata; e um post publicado antes de
#: o coletor existir seria colhido com semanas de vida. A linha e gravada de
#: qualquer jeito (dado colhido vale mais que dado perdido), mas so entra na
#: analise se cair dentro da tolerancia.
AGE_TOLERANCE: dict[str, timedelta] = {
    "h24": timedelta(hours=24),
    "d7": timedelta(days=3),
}

#: Abaixo disso, ``interacoes / alcance`` e ruido: um post com alcance 3 e uma
#: curtida vira 0,33 e domina a media de um slot.
MIN_REACH_FOR_SCORE = 50

CSV_FIELDS: tuple[str, ...] = (
    "collected_at",
    "media_id",
    "item_id",
    "tiktok_id",
    "slot_id",
    "scheduled_at",
    "published_at",
    "age_hours",
    "age_label",
    "is_trial",
    "duration_seconds",
    *REELS_METRICS,
    "error",
)


class InsightsReader(Protocol):
    """So o que este modulo usa.

    Deliberadamente separado do ``Publisher`` de :mod:`.publisher`: aquele
    descreve o caminho de publicacao, e ler metrica nao e isso.
    """

    def media_insights(self, media_id: str, metrics: str) -> dict: ...


def _redact(message: str) -> str:
    """Tira o token de uma mensagem antes de ela virar linha de um CSV commitado.

    ``InstagramError`` ja corta a query string, mas nem todo erro que pode chegar
    aqui e um ``InstagramError`` -- e este arquivo vai para um repositorio
    publico, onde um token vazado vale ate ser revogado a mao.
    """
    return re.sub(r"(access_token=)[^&\s]+", r"\1***", message)


def _parse_time(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def parse_insights(payload: Mapping[str, Any]) -> dict[str, float]:
    """Achata a resposta da Meta em ``{metrica: valor}``.

    A API devolve duas formas conforme a metrica: ``values: [{value: N}]`` e
    ``total_value: {value: N}``. Metrica ausente na resposta fica ausente no
    dict, sem ``KeyError`` -- a propria documentacao avisa que um insight
    indisponivel volta como conjunto vazio em vez de zero, e zero seria mentira.
    """
    flat: dict[str, float] = {}
    for entry in payload.get("data") or []:
        name = entry.get("name")
        if not name:
            continue
        if isinstance(entry.get("total_value"), dict):
            value = entry["total_value"].get("value")
        else:
            values = entry.get("values") or []
            value = values[0].get("value") if values else None
        if isinstance(value, int | float):
            flat[name] = value
    return flat


# ---------------------------------------------------------------------------
# Armazenamento
# ---------------------------------------------------------------------------


def read_snapshots(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def collected_pairs(rows: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    """As chaves ``(media_id, idade)`` ja gravadas. E o estado da coleta."""
    return {(str(row.get("media_id")), str(row.get("age_label"))) for row in rows}


def append_snapshot(path: Path, row: Mapping[str, Any]) -> None:
    """Acrescenta uma linha, escrevendo o cabecalho na primeira vez.

    Append nunca corrompe o que ja esta gravado -- um run morto no meio perde no
    maximo a linha corrente, enquanto reescrever o arquivo inteiro poderia
    trunca-lo.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    novo = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if novo:
            writer.writeheader()
        writer.writerow({campo: row.get(campo, "") for campo in CSV_FIELDS})


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------


def due_for_collection(
    queue: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
    *,
    moment: datetime | None = None,
) -> list[tuple[dict[str, Any], str]]:
    """Pares ``(item, idade)`` que ja tem a idade e ainda nao foram colhidos."""
    agora = moment or queue_mod.now()
    ja = collected_pairs(rows)
    devidos: list[tuple[dict[str, Any], str]] = []
    for item in queue.get("items") or []:
        if item.get("status") != "published":
            continue
        media_id = item.get("instagram_media_id")
        publicado = _parse_time(item.get("published_at"))
        if not media_id or publicado is None:
            continue
        for rotulo, idade in SNAPSHOT_AGES.items():
            if agora - publicado >= idade and (str(media_id), rotulo) not in ja:
                devidos.append((item, rotulo))
    return devidos


def collect(
    queue: Mapping[str, Any],
    publisher: InsightsReader,
    path: Path,
    *,
    moment: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Colhe tudo que esta devido e grava uma linha por coleta."""
    agora = moment or queue_mod.now()
    devidos = due_for_collection(queue, read_snapshots(path), moment=agora)
    resultado: dict[str, Any] = {
        "coletados": 0,
        "erros": 0,
        "devidos": [f"{item['id']}:{rotulo}" for item, rotulo in devidos],
        "dry_run": dry_run,
    }
    if dry_run:
        return resultado

    for item, rotulo in devidos:
        media_id = str(item["instagram_media_id"])
        publicado = _parse_time(item.get("published_at"))
        midia = item.get("media") or {}
        linha: dict[str, Any] = {
            "collected_at": agora.isoformat(),
            "media_id": media_id,
            "item_id": item.get("id"),
            "tiktok_id": item.get("tiktok_id"),
            "slot_id": item.get("slot_id"),
            "scheduled_at": item.get("scheduled_at"),
            "published_at": item.get("published_at"),
            "age_hours": round((agora - publicado).total_seconds() / 3600, 1),
            "age_label": rotulo,
            # Preenchido desde a primeira linha, antes de os reels de teste
            # existirem: uma coluna adicionada depois deixaria todo o historico
            # anterior sem ela, e ai trial e normal ficam inseparaveis.
            "is_trial": "true" if item.get("trial") else "false",
            "duration_seconds": midia.get("duration_seconds"),
        }
        try:
            linha.update(
                parse_insights(publisher.media_insights(media_id, ",".join(REELS_METRICS)))
            )
            resultado["coletados"] += 1
        except Exception as error:  # noqa: BLE001 - um post nao pode derrubar os outros
            # So o tipo e a mensagem, com o token removido: o repositorio e
            # publico e este CSV e commitado.
            linha["error"] = f"{type(error).__name__}: {_redact(str(error))[:200]}"
            resultado["erros"] += 1
        append_snapshot(path, linha)
    return resultado


# ---------------------------------------------------------------------------
# Leitura para o ajuste de horarios
# ---------------------------------------------------------------------------


def _numero(row: Mapping[str, Any], campo: str) -> float | None:
    valor = row.get(campo)
    if valor in (None, ""):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def score(row: Mapping[str, Any]) -> float | None:
    """Interacoes por conta alcancada. ``None`` quando o alcance e pequeno demais."""
    if row.get("error"):
        return None
    alcance = _numero(row, "reach")
    interacoes = _numero(row, "total_interactions")
    if alcance is None or interacoes is None or alcance < MIN_REACH_FOR_SCORE:
        return None
    return interacoes / alcance


def _idade_confiavel(row: Mapping[str, Any], rotulo: str) -> bool:
    idade = _numero(row, "age_hours")
    if idade is None:
        return False
    nominal = SNAPSHOT_AGES[rotulo]
    limite = nominal + AGE_TOLERANCE[rotulo]
    return nominal.total_seconds() / 3600 <= idade <= limite.total_seconds() / 3600


def performance_by_slot(
    rows: Iterable[Mapping[str, Any]],
    *,
    age_label: str = "d7",
    include_trials: bool = False,
) -> dict[str, list[float]]:
    """O ``{slot_id: [scores]}`` que :func:`scheduling.tune_weights` sempre esperou.

    Reels de teste ficam de fora por padrao: eles so alcancam nao seguidores,
    entao a distribuicao de alcance e estruturalmente outra. Misturar os dois
    envenena o ajuste sem que o numero pareca errado.
    """
    por_slot: dict[str, list[float]] = {}
    for row in rows:
        if str(row.get("age_label")) != age_label:
            continue
        if not include_trials and str(row.get("is_trial")).lower() == "true":
            continue
        if not _idade_confiavel(row, age_label):
            continue
        valor = score(row)
        slot = row.get("slot_id")
        if valor is None or not slot:
            continue
        por_slot.setdefault(str(slot), []).append(valor)
    return por_slot
