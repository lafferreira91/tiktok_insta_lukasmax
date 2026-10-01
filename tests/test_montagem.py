"""Montar camera + player do Spotify sem sair fora de sincronia.

O que pode dar errado de verdade e o audio: se a defasagem medida estiver
errada, a boca nao bate com a musica e o video vai para o lixo depois de ja
estar na fila. Os testes montam gravacoes sinteticas com defasagem conhecida e
rodam ffmpeg de verdade, como em test_trim_tail.
"""

from __future__ import annotations

import subprocess

import pytest

pytest.importorskip("av")
np = pytest.importorskip("numpy")
imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")

from lukasmax_automation import media, montagem  # noqa: E402

DEFASAGEM = 2.5


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", *args],
        capture_output=True,
        check=True,
    )


@pytest.fixture(scope="module")
def pasta(tmp_path_factory):
    return tmp_path_factory.mktemp("montagem")


@pytest.fixture(scope="module")
def tela(pasta):
    """Gravacao de tela: 12 s de "musica" (ruido rosa) em proporcao de iPhone."""
    destino = pasta / "tela.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=590x1278:rate=60:duration=12",
        "-f", "lavfi", "-i", "anoisesrc=d=12:c=pink:seed=7:a=0.5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100",
        str(destino),
    )  # fmt: skip
    return destino


def _camera(pasta, tela, nome: str, filtro_audio: str):
    """Camera 720x1280 cujo microfone ouve a musica da tela, com voz por cima."""
    destino = pasta / nome
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=6",
        "-i", str(tela),
        "-f", "lavfi", "-i", "sine=frequency=220:duration=6",
        "-filter_complex", f"[1:a]{filtro_audio},volume=0.6[m];[m][2:a]amix=2:duration=first[a]",
        "-map", "0:v", "-map", "[a]", "-t", "6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(destino),
    )  # fmt: skip
    return destino


@pytest.fixture(scope="module")
def camera(pasta, tela):
    """Camera aberta 2,5 s depois do play no Spotify."""
    return _camera(pasta, tela, "camera.mp4", f"atrim=start={DEFASAGEM},asetpts=PTS-STARTPTS")


class TestASincronia:
    def test_mede_a_defasagem_de_quando_a_camera_abriu(self, camera, tela):
        medida = montagem.medir_defasagem(camera, tela)
        assert medida["defasagem_segundos"] == pytest.approx(DEFASAGEM, abs=0.01)
        assert medida["janelas_concordantes"] == medida["janelas"]

    def test_camera_aberta_antes_do_play_da_defasagem_negativa(self, pasta, tela):
        cedo = _camera(pasta, tela, "cedo.mp4", "adelay=1000:all=1")
        medida = montagem.medir_defasagem(cedo, tela)
        assert medida["defasagem_segundos"] == pytest.approx(-1.0, abs=0.01)

    def test_musicas_diferentes_param_a_montagem(self, pasta, camera):
        outra = pasta / "outra.mp4"
        _ffmpeg(
            "-f", "lavfi", "-i", "testsrc=size=590x1278:rate=60:duration=12",
            "-f", "lavfi", "-i", "anoisesrc=d=12:c=pink:seed=99:a=0.5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(outra),
        )  # fmt: skip
        with pytest.raises(montagem.MontagemError, match="sincronia nao confiavel"):
            montagem.medir_defasagem(camera, outra)


@pytest.fixture(scope="module")
def resultado(pasta, camera, tela):
    saida = pasta / "montado.mp4"
    return saida, montagem.montar(camera, tela, saida)


class TestOReelMontado:
    def test_sai_valido_para_o_instagram(self, resultado):
        saida, _ = resultado
        relatorio = media.validate_for_instagram(saida)
        assert relatorio["valid"], media.failed_checks(relatorio)
        assert (relatorio["media"]["width"], relatorio["media"]["height"]) == (1080, 1920)

    def test_dura_o_mesmo_que_a_camera(self, resultado):
        saida, _ = resultado
        assert media.inspect(saida)["duration_seconds"] == pytest.approx(6.0, abs=0.1)

    def test_o_audio_e_a_musica_limpa_no_ponto_certo(self, resultado, tela, camera):
        saida, _ = resultado
        medida = montagem.medir_defasagem(saida, tela)
        assert medida["defasagem_segundos"] == pytest.approx(DEFASAGEM, abs=0.01)

        # O tom de 220 Hz da "voz" so existe no microfone da camera.
        def tom(path):
            audio = montagem._audio(path)
            espectro = np.abs(np.fft.rfft(audio))
            freqs = np.fft.rfftfreq(len(audio), 1 / montagem.TAXA_AUDIO)
            perto = (freqs > 215) & (freqs < 225)
            return espectro[perto].max() / np.median(espectro)

        assert tom(camera) > 20 * tom(saida)


class TestOInicioDaMusica:
    def test_acha_o_play_depois_do_silencio(self, pasta):
        tela = pasta / "play_atrasado.mp4"
        _ffmpeg(
            "-f", "lavfi", "-i", "testsrc=size=590x1278:rate=30:duration=6",
            "-f", "lavfi", "-i", "anoisesrc=d=6:c=pink:seed=3:a=0.5",
            "-filter_complex", "[1:a]adelay=1500:all=1,atrim=0:6[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(tela),
        )  # fmt: skip
        assert montagem.inicio_da_musica(tela) == pytest.approx(1.5, abs=0.05)

    def test_gravacao_que_ja_comeca_tocando_pede_o_ponto_na_mao(self, tela):
        with pytest.raises(montagem.MontagemError, match="--inicio-musica"):
            montagem.inicio_da_musica(tela)


class TestComLetraEVhs:
    def test_sai_valido_com_a_legenda_queimada(self, pasta, camera, tela):
        letra = {
            "linhas": [
                {"inicio": 0.5, "original": "Hello", "traducao": "olá, sawadika"},
                {"inicio": 3.0, "original": "Bye", "traducao": "lá vai ela"},
            ]
        }
        com, sem = pasta / "com_letra.mp4", pasta / "sem_letra.mp4"
        resultado = montagem.montar(camera, tela, com, letra=letra, inicio_musica=0.0)
        montagem.montar(camera, tela, sem, inicio_musica=0.0)
        assert resultado["letra"]["versos"] == 2
        assert media.validate_for_instagram(com)["valid"]

        # A legenda tem de aparecer de fato: a faixa onde ela fica muda.
        def faixa(path):
            quadro = subprocess.run(
                [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-ss", "4", "-i", str(path),
                 "-frames:v", "1", "-vf", "crop=1080:140:0:1140", "-f", "rawvideo",
                 "-pix_fmt", "gray", "-"],
                capture_output=True, check=True,
            ).stdout  # fmt: skip
            return np.frombuffer(quadro, np.uint8).astype(float)

        assert np.abs(faixa(com) - faixa(sem)).mean() > 2
