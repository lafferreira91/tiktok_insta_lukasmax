from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from .instagram import InstagramPublisher
from .media import normalize_for_instagram, validate_for_instagram
from .queue import publish_due
from .ranking import rank_videos
from .tiktok import download_archive, inventory, save_inventory


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="lukasmax")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit-tiktok")
    audit.add_argument("--input", type=Path)
    audit.add_argument("--output", type=Path, default=Path("data/tiktok_inventory.json"))
    publish = commands.add_parser("publish-due")
    publish.add_argument("--queue", type=Path, default=Path("data/queue.json"))
    publish.add_argument("--media-dir", type=Path, default=Path("media"))
    archive = commands.add_parser("download-archive")
    archive.add_argument("--inventory", type=Path, default=Path("data/tiktok_inventory.json"))
    archive.add_argument("--output", type=Path, default=Path("media/tiktok"))
    archive.add_argument("--archive", type=Path, default=Path("data/downloaded.txt"))
    archive.add_argument("--errors", type=Path, default=Path("reports/download_errors.json"))
    archive.add_argument("--limit", type=int)
    prepare = commands.add_parser("prepare-pilot")
    prepare.add_argument(
        "--source", type=Path, default=Path("media/tiktok/7278034913729907974.mp4")
    )
    prepare.add_argument(
        "--output", type=Path, default=Path("media/ready/7278034913729907974.mp4")
    )
    prepare.add_argument(
        "--report", type=Path, default=Path("reports/media/7278034913729907974-ready.json")
    )
    commands.add_parser("check-instagram")
    commands.add_parser("status")
    args = parser.parse_args()

    if args.command == "audit-tiktok":
        data = json.loads(args.input.read_text()) if args.input else inventory()
        save_inventory(data, args.output)
        ranked = rank_videos(data.get("entries") or [])
        csv_path = args.output.with_name("tiktok_ranking.csv")
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(asdict(ranked[0]).keys()))
            writer.writeheader()
            writer.writerows(asdict(item) for item in ranked)
        print(f"{len(ranked)} videos ranqueados em {csv_path}")
    elif args.command == "publish-due":
        print(json.dumps(publish_due(args.queue, args.media_dir)))
    elif args.command == "download-archive":
        data = json.loads(args.inventory.read_text(encoding="utf-8"))
        ranked = rank_videos(data.get("entries") or [])
        by_id = {str(item.get("id")): item for item in data.get("entries") or []}
        ordered = [by_id[item.id] for item in ranked]
        downloaded, failed = download_archive(
            ordered, args.output, args.archive, args.errors, args.limit
        )
        print(json.dumps({"downloaded": downloaded, "failed": failed}))
    elif args.command == "prepare-pilot":
        report = normalize_for_instagram(args.source, args.output)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
    elif args.command == "check-instagram":
        publisher = InstagramPublisher(
            os.environ["INSTAGRAM_USER_ID"],
            os.environ["INSTAGRAM_ACCESS_TOKEN"],
            os.environ.get("INSTAGRAM_API_VERSION", "v25.0"),
        )
        print(json.dumps(publisher.check_account(), ensure_ascii=False, indent=2))
    elif args.command == "status":
        ready = Path("media/ready/7278034913729907974.mp4")
        queue = json.loads(Path("data/queue.json").read_text(encoding="utf-8"))
        status = {
            "pilot_ready": ready.exists(),
            "pilot_validation": validate_for_instagram(ready) if ready.exists() else None,
            "queue": queue,
            "instagram_user_id_configured": bool(os.environ.get("INSTAGRAM_USER_ID")),
            "instagram_token_configured": bool(os.environ.get("INSTAGRAM_ACCESS_TOKEN")),
            "publishing_enabled": os.environ.get("PUBLISH_ENABLED", "false").lower() == "true",
        }
        print(json.dumps(status, ensure_ascii=False, indent=2))
