"""Command line entry point.

Each command is a ``cmd_*`` function returning an exit code, dispatched from a
table. The previous version was a 47-line if/elif chain with the pilot's video
id hardcoded in four places, which made it both untestable and impossible to
run for any other video.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from . import captions as captions_mod
from . import hosting, planner, scheduling
from . import publisher as publisher_mod
from . import queue as queue_mod
from .paths import Paths
from .ranking import rank_videos


def _emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _paths(args: argparse.Namespace) -> Paths:
    return Paths.resolve(getattr(args, "root", None))


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def cmd_audit_tiktok(args: argparse.Namespace) -> int:
    from .tiktok import inventory, save_inventory

    paths = _paths(args)
    output = args.output or paths.inventory
    data = json.loads(args.input.read_text(encoding="utf-8")) if args.input else inventory()
    save_inventory(data, output)

    ranked = rank_videos(data.get("entries") or [])
    if not ranked:
        print("Nenhum video ranqueavel no inventario", file=sys.stderr)
        return 1
    csv_path = output.with_name("tiktok_ranking.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(ranked[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(item) for item in ranked)
    _emit({"entries": len(data.get("entries") or []), "ranked": len(ranked), "csv": str(csv_path)})
    return 0


def cmd_download_archive(args: argparse.Namespace) -> int:
    from .tiktok import download_archive

    paths = _paths(args)
    data = json.loads((args.inventory or paths.inventory).read_text(encoding="utf-8"))
    entries = data.get("entries") or []
    ranked = rank_videos(entries)
    by_id = {str(entry.get("id")): entry for entry in entries}
    ordered = [by_id[item.id] for item in ranked if item.id in by_id]

    downloaded, failed = download_archive(
        ordered,
        args.output or paths.tiktok_dir,
        args.archive or paths.downloaded,
        args.errors or paths.download_errors,
        args.limit,
        sleep_seconds=args.sleep,
    )
    _emit({"downloaded": downloaded, "failed": failed})
    return 0


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------


def _ranked_ids(paths: Paths) -> list[planner.Candidate]:
    return planner.read_ranking(paths.ranking_csv)


def cmd_draft_captions(args: argparse.Namespace) -> int:
    paths = _paths(args)
    candidates = _ranked_ids(paths)
    if args.ids:
        wanted = set(args.ids)
        candidates = [item for item in candidates if item.tiktok_id in wanted]
    elif args.top:
        candidates = candidates[: args.top]

    generated, cached, skipped = 0, 0, []
    for candidate in candidates:
        info = paths.tiktok_info(candidate.tiktok_id)
        if not info.exists():
            skipped.append({"id": candidate.tiktok_id, "reason": "sem .info.json"})
            continue
        metadata = captions_mod.build_metadata(info, rank=candidate.rank, score=candidate.score)
        record, fresh = captions_mod.draft(
            candidate.tiktok_id, metadata, paths.caption(candidate.tiktok_id), force=args.force
        )
        captions_mod.save(record, paths.caption(candidate.tiktok_id))
        generated += int(fresh)
        cached += int(not fresh)
        if fresh:
            print(f"CAPTION_OK {candidate.tiktok_id}", flush=True)

    _emit({"gerados": generated, "reaproveitados": cached, "pulados": skipped})
    return 0


def cmd_import_captions(args: argparse.Namespace) -> int:
    """Load hand-written captions from a JSON file.

    The escape hatch for working without an ANTHROPIC_API_KEY: the captions are
    written elsewhere and imported here, still passing through the same
    validator and the same approval gate.
    """
    paths = _paths(args)
    entries = json.loads(args.file.read_text(encoding="utf-8"))
    ranking = {item.tiktok_id: item for item in _ranked_ids(paths)}

    imported, blocked = [], []
    for entry in entries:
        video_id = str(entry["tiktok_id"])
        info = paths.tiktok_info(video_id)
        if not info.exists():
            blocked.append({"id": video_id, "reason": "sem .info.json"})
            continue
        candidate = ranking.get(video_id)
        metadata = captions_mod.build_metadata(
            info,
            rank=candidate.rank if candidate else None,
            score=candidate.score if candidate else None,
        )
        record = captions_mod.from_text(
            video_id,
            metadata,
            caption=entry["caption"],
            hashtags=entry.get("hashtags") or [],
            alt_text=entry.get("alt_text") or "",
            author=args.author,
        )
        if args.approve:
            try:
                captions_mod.approve(record)
            except captions_mod.CaptionError as error:
                captions_mod.save(record, paths.caption(video_id))
                blocked.append({"id": video_id, "reason": str(error)})
                continue
        captions_mod.save(record, paths.caption(video_id))
        imported.append({"id": video_id, "status": record["status"]})

    _emit({"importadas": imported, "bloqueadas": blocked})
    return 1 if blocked and not imported else 0


def cmd_review_captions(args: argparse.Namespace) -> int:
    paths = _paths(args)
    records = sorted(paths.captions_dir.glob("*.json")) if paths.captions_dir.exists() else []
    shown = 0
    for path in records:
        record = captions_mod.load(path) or {}
        if args.status and record.get("status") != args.status:
            continue
        shown += 1
        text = record.get("caption") or ""
        tags = " ".join(record.get("hashtags") or [])
        print(f"\n=== {record.get('tiktok_id')} [{record.get('status')}] ===")
        print(f"{text}\n{tags}")
        print(f"({len(text)} caracteres + {len(record.get('hashtags') or [])} hashtags)")
        for warning in record.get("warnings") or []:
            print(f"  AVISO: {warning}")
    if not shown:
        print("Nenhuma legenda encontrada. Rode 'lukasmax draft-captions' primeiro.")
    else:
        print(f"\n{shown} legenda(s). Edite os arquivos em {paths.captions_dir} se quiser ajustar.")
    return 0


def cmd_approve_caption(args: argparse.Namespace) -> int:
    paths = _paths(args)
    targets = (
        [paths.caption(video_id) for video_id in args.ids]
        if args.ids
        else sorted(paths.captions_dir.glob("*.json"))
    )
    approved, blocked = [], []
    for path in targets:
        record = captions_mod.load(path)
        if record is None:
            blocked.append({"id": path.stem, "reason": "arquivo nao existe"})
            continue
        if record.get("status") == "approved" and not args.force:
            continue
        try:
            captions_mod.approve(record, force=args.force)
            captions_mod.save(record, path)
            approved.append(record["tiktok_id"])
        except captions_mod.CaptionError as error:
            captions_mod.save(record, path)
            blocked.append({"id": record.get("tiktok_id"), "reason": str(error)})

    _emit({"aprovadas": approved, "bloqueadas": blocked})
    return 1 if blocked and not approved else 0


# ---------------------------------------------------------------------------
# Media preparation
# ---------------------------------------------------------------------------


def cmd_prepare(args: argparse.Namespace) -> int:
    paths = _paths(args)
    if args.id:
        video_ids = list(args.id)
    elif args.all_approved:
        video_ids = [
            path.stem
            for path in sorted(paths.captions_dir.glob("*.json"))
            if (captions_mod.load(path) or {}).get("status") == "approved"
        ]
    else:
        print("Informe --id <tiktok_id> ou --all-approved", file=sys.stderr)
        return 2

    # Import tardio de proposito: media puxa av e imageio-ffmpeg, que so existem
    # no extra "local". O runner instala so o core e nunca prepara video -- mas
    # se este import ficasse no topo do modulo, ele quebraria ate o 'doctor'.
    from .media import normalize_for_instagram, write_report

    prepared, skipped, failed = [], [], []
    for video_id in video_ids:
        source = paths.tiktok_source(video_id)
        target = paths.ready(video_id)
        if not source.exists():
            failed.append({"id": video_id, "reason": "fonte ausente em media/tiktok"})
            continue
        if target.exists() and not args.force:
            skipped.append(video_id)
            continue
        try:
            report = normalize_for_instagram(source, target)
            write_report(target, paths.ready_report(video_id))
            prepared.append({"id": video_id, "duration": report["media"]["duration_seconds"]})
            print(f"PREPARE_OK {video_id}", flush=True)
        except RuntimeError as error:
            failed.append({"id": video_id, "reason": str(error)})
            print(f"PREPARE_ERROR {video_id}: {error}", flush=True)

    _emit({"preparados": prepared, "ja_prontos": skipped, "falhas": failed})
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Planning and hosting
# ---------------------------------------------------------------------------


def cmd_plan_queue(args: argparse.Namespace) -> int:
    paths = _paths(args)
    start = date.fromisoformat(args.start) if args.start else date.today()
    result = planner.plan_queue(
        paths,
        start=start,
        days=args.days,
        per_day=args.per_day,
        strategy=args.strategy,
    )
    summary = {
        "janela": f"{start.isoformat()} + {args.days} dias",
        "slots_planejados": result["planned_slots"],
        "elegiveis": result["eligible"],
        "a_agendar": len(result["created"]),
        "slots_sobrando": result["unused_slots"],
        "elegiveis_sem_slot": result["unscheduled_eligible"],
        "recusados": planner.summarize_rejections(result["rejected"]),
        "agenda": [
            {"id": item["tiktok_id"], "quando": item["scheduled_at"], "slot": item["slot_id"]}
            for item in result["created"]
        ],
    }
    if args.dry_run:
        summary["dry_run"] = True
        _emit(summary)
        return 0
    summary["gravados"] = planner.commit_plan(result, paths)
    _emit(summary)
    return 0


def cmd_host_media(args: argparse.Namespace) -> int:
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    targets = [
        item
        for item in queue["items"]
        if item.get("status") == "prepared" and (not args.ids or item["tiktok_id"] in set(args.ids))
    ]
    if not targets:
        _emit({"hospedados": [], "nota": "nenhum item em 'prepared'"})
        return 0

    # Mesmo motivo do cmd_prepare: hospedar le a duracao do arquivo local, algo
    # que so acontece no Mac.
    from .media import validate_for_instagram

    slug = hosting.repo_slug()
    hosting.ensure_release(args.tag)
    hosted, failed = [], []
    for item in targets:
        source = paths.ready(item["tiktok_id"])
        try:
            media = hosting.upload_asset(source, args.tag, slug=slug)
        except hosting.HostingError as error:
            failed.append({"id": item["tiktok_id"], "reason": str(error)})
            continue
        media["local_path"] = str(source.relative_to(paths.root))
        media["duration_seconds"] = validate_for_instagram(source)["media"]["duration_seconds"]
        queue_mod.transition(item, "hosted", by="local", note=f"asset em {args.tag}", media=media)
        queue_mod.transition(item, "scheduled", by="local", note="pronto para o cron publicar")
        hosted.append({"id": item["tiktok_id"], "url": media["asset_url"]})
        print(f"HOST_OK {item['tiktok_id']}", flush=True)

    queue_mod.save_queue(queue, paths.queue)
    _emit({"hospedados": hosted, "falhas": failed})
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


def cmd_publish_due(args: argparse.Namespace) -> int:
    paths = _paths(args)
    result = publisher_mod.publish_due(
        paths.queue, paths, max_per_run=args.max_per_run, dry_run=args.dry_run
    )
    _emit(result)
    return 1 if result.get("failed") else 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    stuck = [item for item in queue["items"] if item.get("status") == "publishing"]
    if not stuck:
        _emit({"reconciliados": [], "nota": "nada preso em 'publishing'"})
        return 0
    resolved = publisher_mod.reconcile(queue, publisher_mod.build_publisher(), paths)
    queue_mod.save_queue(queue, paths.queue)
    _emit({"reconciliados": [{"id": item["id"], "status": item["status"]} for item in resolved]})
    return 0


def cmd_check_instagram(args: argparse.Namespace) -> int:
    try:
        publisher = publisher_mod.build_publisher()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    _emit(
        {
            "conta": publisher.check_account(),
            "quota": publisher.content_publishing_limit(),
        }
    )
    return 0


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    due = queue_mod.find_due(queue)
    upcoming = sorted(
        (item for item in queue["items"] if item.get("status") in {"scheduled", "retry"}),
        key=lambda item: str(item.get("scheduled_at")),
    )
    _emit(
        {
            "fila": {
                "total": len(queue["items"]),
                "por_status": queue_mod.counts_by_status(queue),
                "vencidos_agora": [item["id"] for item in due],
                "proximo": (
                    {"id": upcoming[0]["id"], "quando": upcoming[0]["scheduled_at"]}
                    if upcoming
                    else None
                ),
            },
            "publicados_24h": queue_mod.published_last_24h(paths.publish_log),
            "orcamento_24h_restante": queue_mod.daily_budget_left(paths.publish_log),
            "acervo": {
                "baixados": len(
                    [line for line in paths.downloaded.read_text().splitlines() if line.strip()]
                )
                if paths.downloaded.exists()
                else 0,
                "preparados": len(list(paths.ready_dir.glob("*.mp4")))
                if paths.ready_dir.exists()
                else 0,
                "legendas_aprovadas": sum(
                    1
                    for path in paths.captions_dir.glob("*.json")
                    if (captions_mod.load(path) or {}).get("status") == "approved"
                )
                if paths.captions_dir.exists()
                else 0,
            },
            "ambiente": {
                "instagram_user_id": bool(os.environ.get("INSTAGRAM_USER_ID")),
                "instagram_token": bool(os.environ.get("INSTAGRAM_ACCESS_TOKEN")),
                "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "publicacao_ligada": publisher_mod.publishing_enabled(),
            },
        }
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Preflight: everything that must hold before the cron is allowed to post."""
    paths = _paths(args)
    problems: list[str] = []
    notes: dict[str, Any] = {}

    try:
        queue = queue_mod.load_queue(paths.queue)
    except queue_mod.QueueError as error:
        _emit({"ok": False, "problemas": [str(error)]})
        return 1

    stuck = [item["id"] for item in queue["items"] if item.get("status") == "publishing"]
    if stuck:
        problems.append(f"{len(stuck)} item(ns) preso(s) em 'publishing': {', '.join(stuck)}")

    scheduled = [item for item in queue["items"] if item.get("status") in {"scheduled", "retry"}]
    notes["itens_agendados"] = len(scheduled)
    for item in scheduled:
        if not (item.get("caption") or "").strip():
            problems.append(f"{item['id']}: sem legenda congelada")
        media = item.get("media") or {}
        if not media.get("asset_url"):
            problems.append(f"{item['id']}: sem asset_url (rode 'lukasmax host-media')")

    times = [str(item.get("scheduled_at")) for item in scheduled]
    if len(times) != len(set(times)):
        problems.append("ha itens agendados para o mesmo horario")

    if args.check_assets:
        # Contado e reportado de proposito: um "ok" silencioso nao distingue
        # "conferi os 26" de "nao conferi nenhum".
        verified = 0
        for item in scheduled:
            media = item.get("media") or {}
            if not media.get("asset_url"):
                continue
            check = hosting.verify_asset(media["asset_url"], expected_bytes=media.get("bytes"))
            verified += 1
            if not check["ok"]:
                problems.append(f"{item['id']}: asset inacessivel ({check.get('status')})")
        notes["assets_verificados"] = verified

    if publisher_mod.publishing_enabled():
        try:
            publisher = publisher_mod.build_publisher()
            account = publisher.check_account()
            notes["conta"] = account.get("username")
            quota = publisher.content_publishing_limit()
            notes["quota"] = quota
            used = int(quota.get("quota_usage") or 0)
            if used >= queue_mod.DAILY_PUBLISH_LIMIT:
                problems.append(f"quota da Meta esgotada ({used})")
        except Exception as error:
            problems.append(f"nao consegui falar com a Graph API: {error}")
    else:
        notes["publicacao"] = "PUBLISH_ENABLED nao esta true (nada sera postado)"

    notes["orcamento_24h_restante"] = queue_mod.daily_budget_left(paths.publish_log)
    _emit({"ok": not problems, "problemas": problems, "notas": notes})
    return 1 if problems else 0


def cmd_migrate_queue(args: argparse.Namespace) -> int:
    paths = _paths(args)
    if not paths.queue.exists():
        queue_mod.save_queue({"version": queue_mod.SCHEMA_VERSION, "items": []}, paths.queue)
        _emit({"criada": True, "itens": 0})
        return 0

    raw = json.loads(paths.queue.read_text(encoding="utf-8"))
    if raw.get("version") == queue_mod.SCHEMA_VERSION:
        _emit({"ja_migrada": True, "itens": len(raw.get("items") or [])})
        return 0

    backup = paths.queue.with_suffix(".v1.json")
    backup.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    migrated = queue_mod.migrate_v1(raw)
    queue_mod.save_queue(migrated, paths.queue)
    _emit(
        {
            "migrados": len(migrated["items"]),
            "backup": str(backup),
            "por_status": queue_mod.counts_by_status(migrated),
        }
    )
    return 0


def cmd_init_slots(args: argparse.Namespace) -> int:
    paths = _paths(args)
    if paths.slots.exists() and not args.force:
        _emit({"ja_existe": str(paths.slots)})
        return 0
    scheduling.save_slots(scheduling.DEFAULT_CONFIG, paths.slots)
    _emit({"criado": str(paths.slots), "slots": len(scheduling.DEFAULT_SLOTS)})
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "audit-tiktok": cmd_audit_tiktok,
    "download-archive": cmd_download_archive,
    "draft-captions": cmd_draft_captions,
    "import-captions": cmd_import_captions,
    "review-captions": cmd_review_captions,
    "approve-caption": cmd_approve_caption,
    "prepare": cmd_prepare,
    "plan-queue": cmd_plan_queue,
    "host-media": cmd_host_media,
    "publish-due": cmd_publish_due,
    "reconcile": cmd_reconcile,
    "check-instagram": cmd_check_instagram,
    "status": cmd_status,
    "doctor": cmd_doctor,
    "migrate-queue": cmd_migrate_queue,
    "init-slots": cmd_init_slots,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lukasmax")
    parser.add_argument("--root", type=Path, help="Raiz do projeto (default: o repositorio)")
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser("audit-tiktok", help="Inventaria o perfil e ranqueia os videos")
    audit.add_argument("--input", type=Path, help="Reaproveita um inventario ja salvo")
    audit.add_argument("--output", type=Path)

    archive = commands.add_parser("download-archive", help="Baixa os videos que faltam")
    archive.add_argument("--inventory", type=Path)
    archive.add_argument("--output", type=Path)
    archive.add_argument("--archive", type=Path)
    archive.add_argument("--errors", type=Path)
    archive.add_argument("--limit", type=int)
    archive.add_argument("--sleep", type=float, default=4.0, help="Pausa entre downloads")

    drafts = commands.add_parser("draft-captions", help="Gera legendas com IA (local)")
    drafts.add_argument("--top", type=int, help="Apenas os N melhores do ranking")
    drafts.add_argument("--ids", nargs="+")
    drafts.add_argument("--force", action="store_true", help="Regera mesmo se o cache bater")

    imports = commands.add_parser(
        "import-captions", help="Carrega legendas escritas a mao (sem precisar de API de IA)"
    )
    imports.add_argument("--file", type=Path, required=True, help="JSON com as legendas")
    imports.add_argument(
        "--approve", action="store_true", help="Aprova as que passarem no validador"
    )
    imports.add_argument("--author", default="humano", help="Quem escreveu (vai para o registro)")

    review = commands.add_parser("review-captions", help="Mostra as legendas para revisao")
    review.add_argument("--status", choices=["draft", "approved"])

    approve = commands.add_parser("approve-caption", help="Aprova legendas para agendamento")
    approve.add_argument("--ids", nargs="+")
    approve.add_argument("--force", action="store_true", help="Aprova apesar dos avisos")

    prepare = commands.add_parser("prepare", help="Normaliza o video para o formato de Reels")
    prepare.add_argument("--id", nargs="+")
    prepare.add_argument("--all-approved", action="store_true")
    prepare.add_argument("--force", action="store_true")

    plan = commands.add_parser(
        "plan-queue", help="Agenda os videos elegiveis nos melhores horarios"
    )
    plan.add_argument("--days", type=int, default=14)
    plan.add_argument("--per-day", type=int, default=2)
    plan.add_argument("--start", help="Data inicial (YYYY-MM-DD)")
    plan.add_argument("--strategy", choices=["front-loaded", "interleaved"], default="front-loaded")
    plan.add_argument("--dry-run", action="store_true")

    host = commands.add_parser("host-media", help="Sobe os videos como assets de Release")
    host.add_argument("--tag", default="media-v1")
    host.add_argument("--ids", nargs="+")

    publish = commands.add_parser("publish-due", help="Publica o que estiver vencido (CI)")
    publish.add_argument("--max-per-run", type=int, default=1)
    publish.add_argument("--dry-run", action="store_true")

    commands.add_parser("reconcile", help="Resolve itens presos em 'publishing'")
    commands.add_parser("check-instagram", help="Testa o token e mostra a quota")
    commands.add_parser("status", help="Panorama da fila e do acervo")
    commands.add_parser("migrate-queue", help="Converte a fila do schema v1 para v2")

    doctor = commands.add_parser("doctor", help="Checagem completa antes de publicar")
    doctor.add_argument("--check-assets", action="store_true", help="Confere cada URL de midia")

    slots = commands.add_parser("init-slots", help="Cria data/slots.json com os horarios padrao")
    slots.add_argument("--force", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    # .env.local vem primeiro e vence: e a convencao que a maioria das
    # ferramentas usa para o arquivo de segredos de uma maquina so. Sem isso o
    # token fica num arquivo que ninguem le, e o erro que aparece e "variavel
    # ausente" -- que manda procurar no lugar errado.
    load_dotenv(".env.local")
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (queue_mod.QueueError, captions_mod.CaptionError, hosting.HostingError) as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 1
    except FileNotFoundError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
