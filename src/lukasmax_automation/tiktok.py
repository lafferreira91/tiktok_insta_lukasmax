from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL


PROFILE_URL = "https://www.tiktok.com/@_lukasmax"


def inventory(profile_url: str = PROFILE_URL) -> dict[str, Any]:
    options = {
        "extract_flat": True,
        "quiet": True,
        "nocheckcertificate": True,
        "socket_timeout": 60,
        "extractor_retries": 5,
    }
    with YoutubeDL(options) as downloader:
        return downloader.extract_info(profile_url, download=False)


def save_inventory(data: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def best_unwatermarked_format(formats: list[dict[str, Any]]) -> str:
    """Select the best Instagram-compatible format that is not watermarked."""
    candidates = []
    for item in formats:
        note = str(item.get("format_note") or "").lower()
        format_id = str(item.get("format_id") or "")
        codec = str(item.get("vcodec") or "")
        if "watermark" in note or format_id == "download":
            continue
        if codec in {"none", ""}:
            continue
        height = int(item.get("height") or 0)
        bitrate = float(item.get("tbr") or 0)
        candidates.append((height, bitrate, format_id))
    if not candidates:
        raise RuntimeError("Nenhum formato sem marca-d'agua foi encontrado")
    return max(candidates)[2]


def download_without_watermark(url: str, output_template: str) -> Path:
    def clean_format_selector(context: dict[str, Any]):
        formats = context.get("formats") or []
        format_id = best_unwatermarked_format(formats)
        return [next(item for item in formats if str(item.get("format_id")) == format_id)]

    options = {
        "format": clean_format_selector,
        "outtmpl": output_template,
        "writeinfojson": True,
        "nocheckcertificate": True,
        "quiet": True,
        "noprogress": True,
        "socket_timeout": 60,
        "retries": 5,
        "fragment_retries": 5,
    }
    with YoutubeDL(options) as downloader:
        result = downloader.extract_info(url, download=True)
        return Path(downloader.prepare_filename(result))


def download_archive(
    entries: list[dict[str, Any]],
    output_dir: Path,
    archive_path: Path,
    errors_path: Path,
    limit: int | None = None,
) -> tuple[int, int]:
    """Download an inventory safely and resume from a persistent archive."""
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    completed = set()
    if archive_path.exists():
        completed = {line.strip() for line in archive_path.read_text().splitlines() if line.strip()}
    failures_by_id: dict[str, dict[str, str]] = {}
    if errors_path.exists():
        try:
            failures_by_id = {
                str(item["id"]): item for item in json.loads(errors_path.read_text(encoding="utf-8"))
            }
        except (json.JSONDecodeError, KeyError):
            failures_by_id = {}

    def save_failures() -> None:
        errors_path.parent.mkdir(parents=True, exist_ok=True)
        errors_path.write_text(
            json.dumps(list(failures_by_id.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    downloaded = 0
    for item in entries:
        video_id = str(item.get("id") or "")
        if not video_id or video_id in completed:
            continue
        if limit is not None and downloaded >= limit:
            break
        url = str(item.get("webpage_url") or item.get("url") or "")
        if not url.startswith("http"):
            url = f"https://www.tiktok.com/@_lukasmax/video/{video_id}"
        try:
            download_without_watermark(url, str(output_dir / f"{video_id}.%(ext)s"))
            with archive_path.open("a", encoding="utf-8") as handle:
                handle.write(video_id + "\n")
            completed.add(video_id)
            failures_by_id.pop(video_id, None)
            save_failures()
            downloaded += 1
            print(f"DOWNLOAD_OK {video_id}", flush=True)
        except Exception as error:  # continue the archive even if TikTok rejects one item
            failures_by_id[video_id] = {"id": video_id, "url": url, "error": str(error)}
            save_failures()
            print(f"DOWNLOAD_ERROR {video_id}: {error}", flush=True)
    save_failures()
    return downloaded, len(failures_by_id)
