#!/usr/bin/env bash
set -euo pipefail

uv sync --extra dev
uv run pytest
uv run lukasmax audit-tiktok

echo "Ambiente preparado. Para arquivar: uv run lukasmax download-archive"
