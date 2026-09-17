"""Cortar a cartela final sem estragar o arquivo.

Sete dos 194 videos do acervo terminam com a cartela do CapCut. Dois deles ja
tinham sido publicados quando isso foi descoberto (16/09/2026), e eram o pior e
o segundo pior de 54 posts medidos: 356 e 521 views contra mediana de 3.772.

O risco aqui nao e cortar errado -- e gerar um arquivo que o Instagram recusa
depois de ele ja estar na fila, ou cortar tanto que o Reel fique abaixo do
minimo. Por isso os testes rodam ffmpeg de verdade sobre um video sintetico, em
vez de simular a chamada: o que precisa ser verdade e sobre o ARQUIVO que sai.
"""

from __future__ import annotations

import subprocess

import pytest

av = pytest.importorskip("av")
imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")

from lukasmax_automation import media  # noqa: E402


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    """Um Reel sintetico valido de 10s: 1080x1920, 30fps, h264 + aac 48kHz."""
    destino = tmp_path_factory.mktemp("media") / "fonte.mp4"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y",
            "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000",
            str(destino),
        ],
        capture_output=True,
        check=True,
    )  # fmt: skip
    return destino


class TestOCorte:
    def test_o_arquivo_sai_com_a_duracao_pedida(self, video, tmp_path):
        saida = tmp_path / "cortado.mp4"
        media.trim_tail(video, saida, keep_seconds=8.0)
        assert media.inspect(saida)["duration_seconds"] == pytest.approx(8.0, abs=0.15)

    def test_o_corte_cai_no_quadro_pedido_e_nao_no_keyframe(self, video, tmp_path):
        """O motivo de reencodar em vez de usar -c copy.

        7,3s nao e keyframe: com copia de fluxo o arquivo sairia com 6s ou 8s, e
        a cartela de 2s sobreviveria ao corte que existia para remove-la.
        """
        saida = tmp_path / "cortado.mp4"
        media.trim_tail(video, saida, keep_seconds=7.3)
        assert media.inspect(saida)["duration_seconds"] == pytest.approx(7.3, abs=0.15)

    def test_o_arquivo_cortado_continua_valido_para_o_instagram(self, video, tmp_path):
        saida = tmp_path / "cortado.mp4"
        relatorio = media.trim_tail(video, saida, keep_seconds=8.0)
        assert relatorio["valid"], media.failed_checks(relatorio)

    def test_cortado_e_normalizado_saem_com_o_mesmo_formato(self, video, tmp_path):
        """Formato diferente entre os dois viraria variavel escondida na medicao."""
        cortado = media.trim_tail(video, tmp_path / "c.mp4", keep_seconds=8.0)["media"]
        normal = media.normalize_for_instagram(video, tmp_path / "n.mp4")["media"]
        for campo in ("width", "height", "fps", "video_codec", "audio_codec", "audio_hz"):
            assert cortado[campo] == normal[campo], campo


class TestOQueEleRecusa:
    def test_corte_abaixo_do_minimo_do_instagram_e_erro(self, video, tmp_path):
        """2s e menos que o minimo de 3s: o Instagram recusaria na publicacao."""
        with pytest.raises(ValueError, match="minimo"):
            media.trim_tail(video, tmp_path / "curto.mp4", keep_seconds=2.0)

    def test_o_arquivo_curto_demais_nem_chega_a_existir(self, video, tmp_path):
        saida = tmp_path / "curto.mp4"
        with pytest.raises(ValueError):
            media.trim_tail(video, saida, keep_seconds=1.0)
        assert not saida.exists(), "sobrou um arquivo invalido no disco"

    def test_fonte_inexistente_levanta_e_nao_deixa_lixo(self, tmp_path):
        saida = tmp_path / "saida.mp4"
        with pytest.raises(RuntimeError, match="cortar"):
            media.trim_tail(tmp_path / "nao_existe.mp4", saida, keep_seconds=5.0)
        assert not saida.exists()
