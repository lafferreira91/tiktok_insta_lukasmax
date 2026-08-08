from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import imageio_ffmpeg


def probe(path: Path) -> dict:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"]
    result = subprocess.run(command, capture_output=True, text=True)
    return {"ok": result.returncode == 0, "details": result.stderr}


def inspect(path: Path) -> dict:
    result = probe(path)
    details = result["details"]
    duration_match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", details)
    video_match = re.search(
        r"Video: ([^ ,]+).*?, (\d{2,5})x(\d{2,5}).*?, ([\d.]+) fps", details
    )
    audio_match = re.search(r"Audio: ([^ ,]+).*?, (\d+) Hz", details)
    duration = None
    if duration_match:
        duration = (
            int(duration_match.group(1)) * 3600
            + int(duration_match.group(2)) * 60
            + float(duration_match.group(3))
        )
    return {
        "readable": result["ok"],
        "duration_seconds": duration,
        "video_codec": video_match.group(1) if video_match else None,
        "width": int(video_match.group(2)) if video_match else None,
        "height": int(video_match.group(3)) if video_match else None,
        "fps": float(video_match.group(4)) if video_match else None,
        "audio_codec": audio_match.group(1) if audio_match else None,
        "audio_hz": int(audio_match.group(2)) if audio_match else None,
    }


def validate_for_instagram(path: Path) -> dict:
    media = inspect(path)
    checks = {
        "readable": media["readable"],
        "mp4": path.suffix.lower() == ".mp4",
        "duration": media["duration_seconds"] is not None
        and 3 <= media["duration_seconds"] <= 900,
        "video_codec": media["video_codec"] in {"h264", "hevc"},
        "dimensions": media["width"] is not None and media["width"] <= 1920,
        "vertical_9_16": media["width"] is not None
        and media["height"] is not None
        and abs((media["width"] / media["height"]) - (9 / 16)) < 0.01,
        "fps": media["fps"] is not None and 23 <= media["fps"] <= 60,
        "audio_codec": media["audio_codec"] == "aac",
        "audio_48khz": media["audio_hz"] == 48000,
    }
    return {"valid": all(checks.values()), "checks": checks, "media": media}


def normalize_for_instagram(source: Path, target: Path) -> dict:
    """Create a conservative Reels file and remove source-platform metadata."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-map_metadata",
            "-1",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    report = validate_for_instagram(target)
    if not report["valid"]:
        raise RuntimeError(f"Arquivo normalizado falhou na validacao: {report}")
    return report


def extract_review_frames(path: Path, output_dir: Path, seconds: list[int]) -> list[Path]:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for second in seconds:
        target = output_dir / f"frame-{second:03d}.png"
        subprocess.run(
            [ffmpeg, "-y", "-ss", str(second), "-i", str(path), "-frames:v", "1", str(target)],
            check=True,
            capture_output=True,
        )
        frames.append(target)
    return frames


def write_report(path: Path, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(validate_for_instagram(path), ensure_ascii=False, indent=2), encoding="utf-8"
    )
