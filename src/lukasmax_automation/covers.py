"""Escolher a capa do Reel.

O Instagram usa `thumb_offset` -- a posicao em milissegundos do quadro que vira
a capa -- e o padrao e `0`, o primeiro quadro do arquivo. Num video de TikTok
esse quadro quase nunca serve: e transicao, movimento borrado ou uma piscada. O
primeiro Reel publicado saiu assim.

Este modulo varre quadros candidatos e escolhe o mais nitido e melhor exposto.

O que ele NAO faz: detectar olho fechado. Isso exigiria um modelo de marcos
faciais, e prometer que o codigo evita piscadas quando ele so mede nitidez seria
mentira. O que ele faz e tirar a capa do quadro 0 e por num quadro estavel --
o que resolve a maioria dos casos, porque a piscada costuma vir junto com o
movimento que o escore de nitidez ja pune. Para o resto existe o contact sheet
e o `set-cover`, que deixam voce mandar no valor.

Roda so no Mac: o runner recebe o offset ja congelado no item da fila.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Ignora o comeco do video. Os primeiros quadros sao onde moram as transicoes e
#: o corte do TikTok, justamente o material ruim para capa.
SKIP_START_SECONDS = 1.0

#: Nao adianta procurar capa no fim de um video longo: quem para no feed decide
#: pelo que a capa promete do inicio.
SEARCH_WINDOW_SECONDS = 12.0

#: Quantos quadros pontuar. Mais que isso encarece sem mudar a escolha.
CANDIDATE_COUNT = 24

#: Faixa de luminancia media considerada bem exposta (0-255).
GOOD_BRIGHTNESS = (70.0, 185.0)


class CoverError(RuntimeError):
    """Nao consegui analisar o video para escolher a capa."""


@dataclass(frozen=True)
class Candidate:
    offset_ms: int
    sharpness: float
    brightness: float
    score: float


def _sharpness(gray) -> float:
    """Variancia do laplaciano: o medidor classico de foco.

    Quadro borrado (movimento, transicao) tem poucas bordas, entao a variancia
    despenca. E o mesmo motivo pelo qual ele tende a punir o quadro de uma
    virada de cabeca -- que e onde a piscada costuma cair.
    """
    import numpy as np

    g = gray.astype(np.float32)
    lap = 4.0 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def _exposure_penalty(brightness: float) -> float:
    """1.0 para exposicao boa, caindo para os extremos."""
    low, high = GOOD_BRIGHTNESS
    if low <= brightness <= high:
        return 1.0
    distance = low - brightness if brightness < low else brightness - high
    return max(0.15, 1.0 - distance / 90.0)


def candidates(video: Path, *, count: int = CANDIDATE_COUNT) -> list[Candidate]:
    """Pontua quadros espalhados pela janela util do video."""
    import av

    if not video.exists():
        raise CoverError(f"Arquivo nao existe: {video}")

    try:
        with av.open(str(video)) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            duration = float(container.duration / 1_000_000) if container.duration else 0.0
            if duration <= 0:
                raise CoverError(f"Nao consegui ler a duracao de {video.name}")

            start = min(SKIP_START_SECONDS, duration * 0.1)
            end = min(start + SEARCH_WINDOW_SECONDS, duration - 0.1)
            if end <= start:
                start, end = 0.0, max(duration - 0.05, 0.05)
            step = (end - start) / max(count - 1, 1)
            wanted = [start + step * i for i in range(count)]

            found: list[Candidate] = []
            index = 0
            for frame in container.decode(stream):
                if index >= len(wanted):
                    break
                moment = float(frame.time or 0.0)
                if moment + 1e-6 < wanted[index]:
                    continue
                gray = frame.to_ndarray(format="gray")
                sharp = _sharpness(gray)
                bright = float(gray.mean())
                found.append(
                    Candidate(
                        offset_ms=int(moment * 1000),
                        sharpness=sharp,
                        brightness=bright,
                        score=sharp * _exposure_penalty(bright),
                    )
                )
                index += 1
    except av.FFmpegError as error:  # pragma: no cover - depende do arquivo
        raise CoverError(f"Falha ao decodificar {video.name}: {error}") from error

    if not found:
        raise CoverError(f"Nenhum quadro utilizavel em {video.name}")
    return found


def choose(video: Path, *, count: int = CANDIDATE_COUNT) -> Candidate:
    """O melhor quadro da janela util."""
    return max(candidates(video, count=count), key=lambda c: c.score)


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def export_frame(video: Path, offset_ms: int, dest: Path) -> Path:
    """Grava o quadro escolhido como JPEG, para voce conferir antes de publicar."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    command = [
        _ffmpeg(),
        "-y",
        "-ss",
        f"{offset_ms / 1000:.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(dest),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not dest.exists():
        raise CoverError(f"ffmpeg nao gerou {dest.name}: {result.stderr.strip()[-300:]}")
    return dest


def contact_sheet(video: Path, offsets: list[int], dest: Path, *, columns: int = 4) -> Path:
    """Grade com os quadros candidatos, para escolher outro de bate-pronto.

    Sem isso, discordar da escolha automatica viraria tentativa e erro cego.
    """
    if not offsets:
        raise CoverError("Sem offsets para montar o contact sheet")
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = (len(offsets) + columns - 1) // columns
    # Selecao por timestamp, nao por indice de quadro: o indice depende da taxa
    # de quadros do arquivo, o timestamp e o mesmo numero que vai no thumb_offset.
    between = "+".join(f"between(t\\,{ms / 1000:.3f}\\,{ms / 1000 + 0.04:.3f})" for ms in offsets)
    command = [
        _ffmpeg(),
        "-y",
        "-i",
        str(video),
        "-vf",
        f"select='{between}',scale=200:-1,tile={columns}x{rows}",
        "-frames:v",
        "1",
        "-vsync",
        "0",
        "-q:v",
        "4",
        str(dest),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not dest.exists():
        raise CoverError(f"ffmpeg nao gerou o contact sheet: {result.stderr.strip()[-300:]}")
    return dest
