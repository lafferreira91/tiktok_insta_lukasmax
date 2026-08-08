"""Publish normalized videos as GitHub Release assets.

The 1.3 GB of media is deliberately outside git, so the CI runner has no copy of
it. Release assets give every file a public URL that Meta can fetch directly via
``video_url`` -- the runner never transfers a byte, and the repository history
stays JSON and CSV only.

Uploads go through the ``gh`` CLI so we inherit its authentication instead of
managing a token here.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

RELEASE_NOTES = (
    "Videos normalizados para publicacao como Reels.\n\n"
    "Assets gerados por `lukasmax host-media`. Nao editar a mao: a fila em "
    "data/queue.json referencia estes arquivos por nome e sha256."
)


class HostingError(RuntimeError):
    """A release or asset operation failed."""


def _gh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise HostingError(
            "O CLI 'gh' nao esta instalado. Instale com 'brew install gh' e rode 'gh auth login'."
        ) from error
    if check and result.returncode != 0:
        raise HostingError(f"gh {' '.join(args)} falhou: {result.stderr.strip()}")
    return result


def repo_slug() -> str:
    """``owner/repo``, from the CI environment or the local git remote."""
    from_env = os.environ.get("GITHUB_REPOSITORY")
    if from_env:
        return from_env
    result = _gh("repo", "view", "--json", "nameWithOwner")
    return json.loads(result.stdout)["nameWithOwner"]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def asset_url(tag: str, asset_name: str, slug: str | None = None) -> str:
    slug = slug or repo_slug()
    return f"https://github.com/{slug}/releases/download/{tag}/{asset_name}"


def release_exists(tag: str) -> bool:
    return _gh("release", "view", tag, check=False).returncode == 0


def ensure_release(tag: str) -> str:
    """Create the release if it is missing. Safe to call repeatedly."""
    if not release_exists(tag):
        _gh(
            "release",
            "create",
            tag,
            "--title",
            f"Midia {tag}",
            "--notes",
            RELEASE_NOTES,
        )
    return tag


def upload_asset(path: Path, tag: str, *, slug: str | None = None) -> dict[str, Any]:
    """Upload one normalized MP4 and return everything the queue needs to cite it."""
    if not path.exists():
        raise HostingError(f"Arquivo nao existe: {path}")
    ensure_release(tag)
    # --clobber makes re-hosting a re-normalized file idempotent.
    _gh("release", "upload", tag, str(path), "--clobber")
    slug = slug or repo_slug()
    return {
        "release_tag": tag,
        "asset_name": path.name,
        "asset_url": asset_url(tag, path.name, slug),
        "sha256": sha256_of(path),
        "bytes": path.stat().st_size,
    }


def verify_asset(
    url: str, *, expected_bytes: int | None = None, timeout: int = 30
) -> dict[str, Any]:
    """Confirm the asset is publicly reachable and the right size.

    GitHub answers the release-download URL with a 302 to a storage host, which
    is exactly what Meta's fetcher has to follow -- so this doubles as a check
    that the ``video_url`` path will work.
    """
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            size = int(length) if length else None
            ok = response.status == 200 and (expected_bytes is None or size == expected_bytes)
            return {
                "ok": ok,
                "status": response.status,
                "bytes": size,
                "final_url": response.geturl(),
                "redirected": response.geturl() != url,
            }
    except urllib.error.HTTPError as error:
        return {"ok": False, "status": error.code, "bytes": None, "error": str(error.reason)}
    except urllib.error.URLError as error:
        return {"ok": False, "status": None, "bytes": None, "error": str(error.reason)}


def fetch_asset(url: str, dest: Path, *, expected_sha256: str | None = None) -> Path:
    """Download an asset locally. Only used by the resumable upload fallback."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=300) as response, dest.open("wb") as handle:
            while block := response.read(1024 * 1024):
                handle.write(block)
    except (urllib.error.HTTPError, urllib.error.URLError) as error:
        raise HostingError(f"Nao consegui baixar {url}: {error}") from error
    if expected_sha256:
        actual = sha256_of(dest)
        if actual != expected_sha256:
            dest.unlink(missing_ok=True)
            raise HostingError(
                f"sha256 divergente para {dest.name}: esperado {expected_sha256[:12]}, "
                f"veio {actual[:12]}. O asset foi trocado ou corrompido."
            )
    return dest
