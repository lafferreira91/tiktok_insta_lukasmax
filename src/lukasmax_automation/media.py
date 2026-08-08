"""Inspect, validate and normalize video for Instagram Reels.

Metadata comes from PyAV, which reads the container header directly. The previous
implementation ran ``ffmpeg -f null -`` and scraped its stderr with regexes,
which meant fully decoding the video just to learn its resolution -- twice per
normalization -- and silently returning ``None`` whenever an ffmpeg build
changed its log wording.
"""

from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path

import av
import imageio_ffmpeg

#: Instagram rejects anything outside these. 9:16 is enforced tightly because a
#: Reel that is even slightly off gets letterboxed.
MIN_DURATION_SECONDS = 3
MAX_DURATION_SECONDS = 900
MAX_WIDTH = 1920
TARGET_ASPECT = 9 / 16
ASPECT_TOLERANCE = 0.01
MIN_FPS = 23
MAX_FPS = 60
REQUIRED_AUDIO_HZ = 48000


def _rotation(stream: av.video.stream.VideoStream) -> int:
    """Rotation in degrees, from container metadata.

    A phone-shot video is often stored landscape with a 90 degree rotation flag,
    so the raw width and height are the wrong way round. Ignoring this makes a
    perfectly good vertical video fail the 9:16 check.
    """
    for source in (stream.metadata, getattr(stream, "side_data", None) or {}):
        try:
            value = source.get("rotate") or source.get("DISPLAYMATRIX")
        except AttributeError:
            continue
        if value in (None, ""):
            continue
        try:
            return int(round(float(str(value).strip().split()[-1]))) % 360
        except (TypeError, ValueError):
            continue
    return 0


def inspect(path: Path) -> dict:
    """Container metadata, read from the header without decoding any frames."""
    media = {
        "readable": False,
        "duration_seconds": None,
        "video_codec": None,
        "width": None,
        "height": None,
        "fps": None,
        "audio_codec": None,
        "audio_hz": None,
        "bitrate": None,
        "has_audio": False,
        "rotation": 0,
    }
    try:
        with av.open(str(path)) as container:
            video_streams = container.streams.video
            if not video_streams:
                return media
            video = video_streams[0]
            media["readable"] = True
            media["bitrate"] = container.bit_rate or None

            duration = None
            if container.duration:
                duration = container.duration / av.time_base
            elif video.duration and video.time_base:
                duration = float(video.duration * video.time_base)
            media["duration_seconds"] = round(float(duration), 3) if duration else None

            media["video_codec"] = video.codec_context.name
            width, height = video.codec_context.width, video.codec_context.height
            rotation = _rotation(video)
            media["rotation"] = rotation
            if rotation in (90, 270):
                width, height = height, width
            media["width"], media["height"] = width, height

            rate = video.average_rate or video.guessed_rate
            media["fps"] = round(float(Fraction(rate)), 3) if rate else None

            if container.streams.audio:
                audio = container.streams.audio[0]
                media["has_audio"] = True
                media["audio_codec"] = audio.codec_context.name
                media["audio_hz"] = audio.codec_context.sample_rate
    except (av.AVError, OSError, ValueError):
        # An unreadable file is a validation result, not a crash -- the caller
        # decides whether to re-download or skip it.
        return media
    return media


def probe(path: Path) -> dict:
    """Back-compat shim for callers that only want a readable/unreadable answer."""
    media = inspect(path)
    return {"ok": media["readable"], "details": media}


def validate_for_instagram(path: Path) -> dict:
    media = inspect(path)
    width, height, fps = media["width"], media["height"], media["fps"]
    duration = media["duration_seconds"]
    checks = {
        "readable": media["readable"],
        "mp4": path.suffix.lower() == ".mp4",
        "duration": duration is not None
        and MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS,
        "video_codec": media["video_codec"] in {"h264", "hevc"},
        "dimensions": width is not None and width <= MAX_WIDTH,
        "vertical_9_16": (
            width is not None
            and height not in (None, 0)
            and abs((width / height) - TARGET_ASPECT) < ASPECT_TOLERANCE
        ),
        "fps": fps is not None and MIN_FPS <= fps <= MAX_FPS,
        "audio_codec": media["audio_codec"] == "aac",
        "audio_48khz": media["audio_hz"] == REQUIRED_AUDIO_HZ,
    }
    return {"valid": all(checks.values()), "checks": checks, "media": media}


def failed_checks(report: dict) -> list[str]:
    return sorted(name for name, passed in report["checks"].items() if not passed)


def normalize_for_instagram(source: Path, target: Path) -> dict:
    """Create a conservative Reels file and strip source-platform metadata."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
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
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"ffmpeg falhou ao normalizar {source.name}:\n{tail}")

    report = validate_for_instagram(target)
    if not report["valid"]:
        raise RuntimeError(
            f"Arquivo normalizado {target.name} falhou em: {', '.join(failed_checks(report))}"
        )
    return report


def extract_review_frames(path: Path, output_dir: Path, seconds: list[int]) -> list[Path]:
    """Grab one PNG per requested second, for eyeballing watermarks."""
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


def write_report(path: Path, report_path: Path) -> dict:
    report = validate_for_instagram(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report
