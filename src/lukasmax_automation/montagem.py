"""Monta um Reel novo: gravacao da camera + faixa do player do Spotify por cima.

Reproduz a montagem que era feita a mao no CapCut:

1. a gravacao de tela do Spotify entra como segunda camada;
2. uma mascara deixa so a faixa entre o titulo da musica e os botoes do player;
3. contraste no maximo e saturacao no minimo, para sobrar so o texto branco;
4. mesclagem "Clarear" (lighten): o fundo escuro some e o branco fica por cima;
5. os dois audios sao sincronizados e so o da gravacao de tela fica -- e a
   musica limpa, sem o microfone do carro;
6. a letra traduzida entra acima do titulo (ver ``letras``);
7. por cima de tudo, o filtro de VHS fraquinho que era passado depois no app.

A sincronia e o passo que dava trabalho no CapCut. Aqui ela sai da correlacao
cruzada dos dois audios: o microfone da camera captou a mesma musica que a
gravacao de tela tem limpa, entao o pico da correlacao e a defasagem exata.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

import imageio_ffmpeg
import numpy as np

from . import letras
from .media import _ENCODE, failed_checks, inspect, validate_for_instagram
from .paths import REPO_ROOT

LARGURA, ALTURA = 1080, 1920
FONTES = REPO_ROOT / "assets" / "fonts"

#: A faixa do player na gravacao de tela, em fracao da altura. Medida numa
#: gravacao de iPhone (1180x2556) com o Spotify na tela do player: comeca logo
#: abaixo do botao "Mudar para video" e termina embaixo dos botoes de controle.
#: Em fracao, e nao em pixel, para sobreviver a outro iPhone com a mesma tela.
FAIXA_TOPO = 0.6514
FAIXA_ALTURA = 0.2230

#: Onde a faixa entra no Reel. Copiado dos videos ja publicados: o titulo fica
#: um pouco abaixo do meio e os botoes terminam antes da area que a interface
#: do Instagram cobre embaixo.
FAIXA_LARGURA = 960
FAIXA_Y = 1240

#: "Contraste no maximo, saturacao no minimo". Contraste no maximo de verdade
#: apagava o nome do artista e a trilha da barra de progresso, que sao cinza no
#: Spotify; esta curva zera o fundo vermelho escuro e ainda deixa os cinzas
#: legiveis, como nos videos antigos.
CURVA = "0/0 0.22/0 0.5/0.85 1/1"

#: O filtro de VHS, medido nos Reels ja publicados em vez de chutado: nas bordas
#: da legenda branca de tres videos, o vermelho sai ~0,5 px a esquerda e ~1,2 px
#: acima do verde, e o azul ~1,4 px a esquerda. O deslocamento e aplicado com o
#: quadro em 2x, porque o rgbashift so anda de pixel inteiro e meio pixel ja
#: muda o desenho da franja. Os valores (em pixels do quadro 2x) sao maiores que
#: a medida porque a volta para 1080 suaviza a franja; foram calibrados medindo
#: a saida do mesmo jeito que os videos antigos.
VHS = ",".join(
    [
        f"scale={LARGURA * 2}:{ALTURA * 2}:flags=bicubic",
        "rgbashift=rh=-2:rv=-3:bh=-3:edge=smear",
        f"scale={LARGURA}:{ALTURA}:flags=bicubic",
        # Nitidez: contorno mais marcado, so na luminancia.
        "unsharp=5:5:0.8:5:5:0",
        "eq=saturation=1.18",
        # Ceu mais azul: satura so os tons ciano e azul, sem tingir o resto.
        "huesaturation=saturation=0.35:colors=c+b:strength=2",
    ]
)

TAXA_AUDIO = 8000
JANELA_SEGUNDOS = 4.0
RAIO_SEGUNDOS = 0.5
TOLERANCIA_SEGUNDOS = 0.02
#: -40 dB abaixo do volume tipico da musica conta como silencio.
LIMIAR_SOM = 0.01


class MontagemError(RuntimeError):
    pass


def _ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def _audio(path: Path) -> np.ndarray:
    """Audio mono em 8 kHz: sobra resolucao para sincronizar a 1/8000 s."""
    result = subprocess.run(
        [_ffmpeg(), "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(TAXA_AUDIO), "-f", "f32le", "-"],
        capture_output=True,
    )  # fmt: skip
    if result.returncode != 0 or not result.stdout:
        raise MontagemError(f"nao consegui ler o audio de {path.name}")
    audio = np.frombuffer(result.stdout, dtype=np.float32).astype(np.float64)
    return audio - audio.mean()


def _melhor_lag(referencia: np.ndarray, trecho: np.ndarray) -> tuple[int, float]:
    """Deslocamento (em amostras) do ``trecho`` dentro da ``referencia``.

    Correlacao cruzada completa via FFT. Devolve o lag e a forca do pico sobre a
    mediana, que separa um alinhamento real de um pico qualquer no ruido.
    """
    n = len(referencia) + len(trecho) - 1
    nfft = 1 << (n - 1).bit_length()
    espectro = np.fft.rfft(referencia, nfft) * np.conj(np.fft.rfft(trecho, nfft))
    circular = np.fft.irfft(espectro, nfft)
    # Reordena para o indice i valer o lag i - (len(trecho) - 1).
    completa = np.abs(np.concatenate([circular[-(len(trecho) - 1) :], circular[: len(referencia)]]))
    pico = int(np.argmax(completa))
    forca = float(completa[pico] / (np.median(completa) + 1e-12))
    return pico - (len(trecho) - 1), forca


def medir_defasagem(camera: Path, tela: Path) -> dict:
    """Quantos segundos da gravacao de tela ja tinham passado quando a camera comecou.

    Positivo e o caso normal: da o play no Spotify, depois abre a camera. A
    medida global e conferida em janelas de 4 s ao longo do clipe. Uma janela
    ou outra discorda quando a voz cobre a musica, mas se a maioria nao bate, o
    alinhamento nao e confiavel e a montagem para em vez de sair fora de sincronia.
    """
    audio_camera, audio_tela = _audio(camera), _audio(tela)
    lag, _ = _melhor_lag(audio_tela, audio_camera)

    # Cada janela procura o encaixe so perto da defasagem global. Procurando na
    # gravacao inteira, um refrao ou uma batida repetida casa com outro compasso
    # e a janela "discorda" sem que a sincronia esteja errada. Com musicas
    # diferentes, o pico de cada janela cai em qualquer lugar do raio e discorda.
    janela = int(JANELA_SEGUNDOS * TAXA_AUDIO)
    raio = int(RAIO_SEGUNDOS * TAXA_AUDIO)
    tolerancia = int(TOLERANCIA_SEGUNDOS * TAXA_AUDIO)
    # Se a camera abriu antes do play, o comeco dela nao tem par na tela.
    primeiro = max(0, raio - lag)
    inicios = [
        inicio
        for inicio in range(primeiro, max(len(audio_camera) - janela + 1, primeiro + 1), janela)
        if inicio + lag + janela + raio <= len(audio_tela)
    ]
    concordantes = 0
    for inicio in inicios:
        vizinhanca = audio_tela[inicio + lag - raio : inicio + lag + janela + raio]
        lag_janela, _ = _melhor_lag(vizinhanca, audio_camera[inicio : inicio + janela])
        if abs(lag_janela - raio) <= tolerancia:
            concordantes += 1

    medida = {
        "defasagem_segundos": round(lag / TAXA_AUDIO, 4),
        "janelas_concordantes": concordantes,
        "janelas": len(inicios),
    }
    if concordantes * 2 <= len(inicios):
        raise MontagemError(
            f"sincronia nao confiavel: so {concordantes} de {len(inicios)} trechos "
            f"concordam com {medida['defasagem_segundos']}s. Confira se as duas "
            "gravacoes sao da mesma musica, ou passe --defasagem na mao."
        )
    return medida


def inicio_da_musica(tela: Path) -> float:
    """Em que segundo da gravacao de tela a musica comecou a tocar.

    E o que liga o relogio da letra (segundos da musica) ao do video. A gravacao
    normalmente comeca em silencio e o play vem depois; o primeiro trecho de
    20 ms acima de -40 dB e o comeco da musica. Se ja comeca tocando, nao ha
    como saber em que ponto da musica estava, e a legenda sairia fora de tempo.
    """
    audio = _audio(tela)
    passo = int(0.02 * TAXA_AUDIO)
    quadros = len(audio) // passo
    # Desvio padrao por trecho, e nao RMS: um nivel DC constante (que _audio
    # desloca ao tirar a media do arquivo inteiro) faria o silencio parecer som.
    volume = audio[: quadros * passo].reshape(quadros, passo).std(axis=1)
    tocando = np.flatnonzero(volume > LIMIAR_SOM * np.percentile(volume, 95))
    if not len(tocando) or tocando[0] < 3:
        raise MontagemError(
            f"{tela.name} ja comeca com a musica tocando, entao nao da para saber em "
            "que ponto da musica ela estava. Passe --inicio-musica na mao."
        )
    return round(float(tocando[0] * passo / TAXA_AUDIO), 3)


def _filtro(*, com_letra: bool, vhs: bool) -> str:
    faixa = (
        f"crop=iw:ih*{FAIXA_ALTURA}:0:ih*{FAIXA_TOPO},"
        f"hue=s=0,curves=all='{CURVA}',"
        f"scale={FAIXA_LARGURA}:-2:flags=lanczos,"
        f"pad={LARGURA}:{ALTURA}:({LARGURA}-iw)/2:{FAIXA_Y}:black"
    )
    # A letra entra antes do VHS: nos videos antigos a legenda tem a mesma
    # franja colorida que o resto, entao o filtro era passado no video pronto.
    acabamento = [
        *(["subtitles=letra.ass:fontsdir=fontes"] if com_letra else []),
        *([VHS] if vhs else []),
        "format=yuv420p",
    ]
    # Mesclagem em RGB: "lighten" em YUV compara os canais de cor separadamente
    # e tingiria a imagem. Em RGB e o mesmo "Clarear" do CapCut.
    #
    # setpts zera o relogio das duas camadas. Depois do -ss, o primeiro quadro
    # da gravacao de tela chega em 1/30 s, e o blend soltava o quadro 0 so com a
    # camera -- um quadro sem o player no comeco de todo Reel.
    return (
        f"[0:v]setpts=PTS-STARTPTS,fps=30,"
        f"scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={LARGURA}:{ALTURA},setsar=1,format=gbrp[camera];"
        f"[1:v]setpts=PTS-STARTPTS,fps=30,{faixa},setsar=1,format=gbrp[faixa];"
        f"[camera][faixa]blend=all_mode=lighten:shortest=1,{','.join(acabamento)}[video]"
    )


def montar(
    camera: Path,
    tela: Path,
    destino: Path,
    *,
    defasagem: float | None = None,
    letra: dict[str, Any] | None = None,
    inicio_musica: float | None = None,
    vhs: bool = True,
) -> dict:
    """Gera o Reel montado em ``destino`` e devolve a medida e a validacao.

    ``inicio_musica`` e o segundo da gravacao de tela em que a musica comecou;
    so importa com ``letra``, e e detectado pelo audio quando nao vem.
    """
    medida = (
        {"defasagem_segundos": defasagem, "janelas_concordantes": None, "janelas": None}
        if defasagem is not None
        else medir_defasagem(camera, tela)
    )
    offset = medida["defasagem_segundos"]
    # Defasagem negativa: a camera comecou antes da musica. Corta o comeco da
    # camera em vez de pedir um trecho da gravacao de tela que nao existe.
    pula_camera, pula_tela = max(-offset, 0.0), max(offset, 0.0)
    duracao = min(
        (inspect(camera)["duration_seconds"] or 0) - pula_camera,
        (inspect(tela)["duration_seconds"] or 0) - pula_tela,
    )

    resultado: dict[str, Any] = {"sincronia": medida, "duracao_segundos": round(duracao, 3)}
    with tempfile.TemporaryDirectory() as pasta:
        # O ffmpeg roda dentro desta pasta e acha a legenda e as fontes por nome
        # relativo. Caminho absoluto dentro de um filtro exige escapar ":" e
        # aspas em dois niveis -- e o caminho da gravacao de tela tem espaco.
        trabalho = Path(pasta)
        if letra is not None:
            if inicio_musica is None:
                inicio_musica = inicio_da_musica(tela)
            posicao = pula_tela - inicio_musica
            versos = letras.eventos(letra, inicio_musica=posicao, duracao=duracao)
            (trabalho / "letra.ass").write_text(
                letras.gerar_ass(versos, LARGURA, ALTURA), encoding="utf-8"
            )
            (trabalho / "fontes").mkdir()
            for fonte in FONTES.glob("*.ttf"):
                (trabalho / "fontes" / fonte.name).write_bytes(fonte.read_bytes())
            resultado["letra"] = {"inicio_musica": inicio_musica, "versos": len(versos)}

        destino = destino.resolve()
        destino.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [_ffmpeg(), "-y",
             "-ss", f"{pula_camera:.4f}", "-i", str(camera.resolve()),
             "-ss", f"{pula_tela:.4f}", "-i", str(tela.resolve()),
             "-filter_complex", _filtro(com_letra=letra is not None, vhs=vhs),
             "-map", "[video]", "-map", "1:a",
             "-t", f"{duracao:.3f}",
             *_ENCODE, str(destino)],
            capture_output=True,
            text=True,
            cwd=trabalho,
        )  # fmt: skip
    if result.returncode != 0:
        destino.unlink(missing_ok=True)
        tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise MontagemError(f"ffmpeg falhou ao montar {destino.name}:\n{tail}")

    report = validate_for_instagram(destino)
    if not report["valid"]:
        raise MontagemError(f"{destino.name} saiu invalido: {', '.join(failed_checks(report))}")
    return {**resultado, "validacao": report}
