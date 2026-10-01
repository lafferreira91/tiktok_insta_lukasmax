"""A letra traduzida acima do titulo: tempo certo e nenhuma frase faltando.

Os dois erros que estragam o Reel sao a frase fora de tempo (desloca a letra
inteira) e um verso sem traducao que simplesmente nao aparece. Os testes cobrem
a conversao do LRC e o recorte do trecho gravado, sem rede.
"""

from __future__ import annotations

import pytest

from lukasmax_automation import letras

LRC = """[00:00.55]Hello Sawadika
[00:01.82]Yeah you know my wrist is cold
[00:03.56]Got this time piece from Geneva
[00:28.44]
[00:31.47]Who let this bitch out of the cage
"""


def _traduzida(**traducoes: str) -> dict:
    linhas = letras.de_lrc(LRC)
    for linha in linhas:
        linha["traducao"] = traducoes.get(linha["original"], f"pt {linha['original']}")
    return {"linhas": linhas}


class TestOLrc:
    def test_le_tempo_e_texto(self):
        linhas = letras.de_lrc(LRC)
        assert linhas[0] == {"inicio": 0.55, "original": "Hello Sawadika", "traducao": ""}
        assert linhas[-1]["inicio"] == pytest.approx(31.47)

    def test_linha_vazia_fica_como_marcador_de_fim(self):
        assert [linha["original"] for linha in letras.de_lrc(LRC)][3] == ""

    def test_slug_sem_acento_nem_espaco(self):
        assert letras.slug("Beyoncé", "Crazy In Love") == "beyonce-crazy-in-love"


class TestOTrechoGravado:
    def test_tempos_viram_segundos_do_video_com_a_troca_adiantada(self):
        # O video comeca no segundo 1.0 da musica; cada verso entra 0,1 s antes.
        eventos = letras.eventos(_traduzida(), inicio_musica=1.0, duracao=10)
        antes = letras.ANTECIPACAO
        assert eventos[0] == (0.0, pytest.approx(0.82 - antes), "pt Hello Sawadika")
        assert eventos[1][:2] == (pytest.approx(0.82 - antes), pytest.approx(2.56 - antes))

    def test_video_abre_com_legenda_mesmo_antes_do_primeiro_verso(self):
        # A musica so canta 2 s depois do primeiro quadro: a frase ja entra no 0.
        registro = {"linhas": [{"inicio": 5.0, "original": "Hi", "traducao": "oi"}]}
        eventos = letras.eventos(registro, inicio_musica=3.0, duracao=10)
        assert eventos == [(0.0, pytest.approx(2.0 - letras.ANTECIPACAO + 6.0), "oi")]

    def test_ajuste_corrige_um_lrc_atrasado(self):
        registro = {**_traduzida(), "ajuste": -0.5}
        sem = letras.eventos(_traduzida(), inicio_musica=0, duracao=10)
        com = letras.eventos(registro, inicio_musica=0, duracao=10)
        assert com[1][0] == pytest.approx(sem[1][0] - 0.5)

    def test_verso_antes_do_instrumental_nao_fica_parado(self):
        eventos = letras.eventos(_traduzida(), inicio_musica=0, duracao=60)
        comeco, fim, _ = eventos[2]
        assert fim - comeco == pytest.approx(letras.DURACAO_MAXIMA)

    def test_nada_passa_do_fim_do_video(self):
        eventos = letras.eventos(_traduzida(), inicio_musica=0, duracao=2.0)
        assert [texto for *_, texto in eventos] == [
            "pt Hello Sawadika",
            "pt Yeah you know my wrist is cold",
        ]
        assert eventos[-1][1] == pytest.approx(2.0)

    def test_verso_sem_traducao_no_trecho_para_a_montagem(self):
        registro = _traduzida(**{"Got this time piece from Geneva": ""})
        with pytest.raises(letras.LetraError, match="Geneva"):
            letras.eventos(registro, inicio_musica=0, duracao=10)

    def test_verso_sem_traducao_fora_do_trecho_nao_importa(self):
        registro = _traduzida(**{"Who let this bitch out of the cage": ""})
        assert len(letras.eventos(registro, inicio_musica=0, duracao=10)) == 3


class TestAQuebra:
    def test_linhas_equilibradas_e_nao_cinco_palavras_e_uma(self):
        assert letras.quebrar("olhando para a página em branco na sua frente") == [
            "olhando para a página",
            "em branco na sua frente",
        ]

    def test_frase_curta_fica_numa_linha(self):
        assert letras.quebrar("ninguém mais pode tirar essas") == ["ninguém mais pode tirar essas"]

    def test_quebra_na_pontuacao_quando_da(self):
        assert letras.quebrar("é demais? sou dramática demais?") == [
            "é demais?",
            "sou dramática demais?",
        ]

    def test_nao_termina_linha_em_palavra_presa_a_seguinte(self):
        for frase in (
            "vivo pelo choque como se fosse alérgica",
            "baht entrando na conta até eu sair com a bolsa cheia",
            "quem soltou essa fera da jaula?",
        ):
            for linha in letras.quebrar(frase)[:-1]:
                assert linha.split()[-1] not in letras.PALAVRAS_PRESAS, (frase, linha)

    def test_acento_distingue_verbo_de_preposicao(self):
        # "dá" pode fechar a linha; tirar o acento o confundiria com "da".
        assert letras.quebrar("tão perto que dá até pra sentir o gosto") == [
            "tão perto que dá",
            "até pra sentir o gosto",
        ]

    def test_barra_forca_a_quebra(self):
        assert letras.quebrar("dou show quando chego, | cavalo de pau num tuk-tuk") == [
            "dou show quando chego,",
            "cavalo de pau num tuk-tuk",
        ]

    def test_nenhuma_linha_passa_do_limite(self):
        frase = "sou braba, braba demais, melhor chamar um médico agora mesmo por favor"
        assert all(len(linha) <= letras.MAX_CARACTERES for linha in letras.quebrar(frase))


class TestAPosicao:
    @staticmethod
    def _alturas(texto):
        ass = letras.gerar_ass([(0, 1, texto)], 1080, 1920)
        return [
            int(linha.split("\\pos(540,")[1].split(")")[0])
            for linha in ass.splitlines()
            if linha.startswith("Dialogue")
        ]

    def test_bloco_centralizado_com_uma_ou_duas_linhas(self):
        (uma,) = self._alturas("olá, sawadika")
        cima, baixo = self._alturas("olhando para a página em branco na sua frente")
        assert baixo - cima == letras.ESPACO_LINHAS
        assert (cima + baixo) / 2 == uma

    def test_chaves_no_texto_nao_viram_comando_ass(self):
        ass = letras.gerar_ass([(0, 1, "oi {\\b1}")], 1080, 1920)
        assert "oi \\{\\\\b1\\}" in ass
