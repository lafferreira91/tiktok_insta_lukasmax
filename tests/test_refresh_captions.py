"""Trocar a legenda de um post que ainda nao foi ao ar.

A legenda e congelada no item da fila justamente para que editar
``data/captions/`` nunca altere sozinho um post agendado. Trocar de proposito
precisa entao de um caminho explicito, e a parte que importa e o que ele se
recusa a tocar: o que ja foi publicado nao volta atras.
"""

from __future__ import annotations

import json

from conftest import make_item, make_queue

from lukasmax_automation import captions as captions_mod
from lukasmax_automation import planner


def escrever_legenda(paths, video_id: str, texto: str, *, status: str = "approved") -> None:
    paths.captions_dir.mkdir(parents=True, exist_ok=True)
    paths.caption(video_id).write_text(
        json.dumps(
            {
                "tiktok_id": video_id,
                "caption": texto,
                "hashtags": ["#Um", "#Dois", "#Tres"],
                "alt_text": "descricao",
                "status": status,
                "input_fingerprint": f"sha256:{texto[:6]}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class TestOQueMuda:
    def test_um_item_agendado_recebe_a_legenda_nova(self, paths):
        queue = make_queue(make_item("111"))
        escrever_legenda(paths, "111", "legenda nova com sarcasmo")

        resultado = planner.refresh_captions(queue, paths)

        assert len(resultado["trocadas"]) == 1
        assert "legenda nova" in queue["items"][0]["caption"]

    def test_a_impressao_digital_acompanha_o_texto(self, paths):
        """Sem isso a fila diz que a legenda e uma e o registro diz que e outra."""
        queue = make_queue(make_item("111"))
        escrever_legenda(paths, "111", "outra legenda qualquer aqui")

        planner.refresh_captions(queue, paths)

        record = captions_mod.load(paths.caption("111"))
        assert queue["items"][0]["caption_fingerprint"] == record["input_fingerprint"]

    def test_texto_identico_nao_conta_como_troca(self, paths):
        queue = make_queue(make_item("111"))
        escrever_legenda(paths, "111", "igual")
        planner.refresh_captions(queue, paths)

        segunda = planner.refresh_captions(queue, paths)

        assert segunda["trocadas"] == []

    def test_da_para_mirar_em_um_video_so(self, paths):
        queue = make_queue(make_item("111"), make_item("222"))
        escrever_legenda(paths, "111", "so esta deveria mudar aqui")
        escrever_legenda(paths, "222", "esta nao pode mudar agora")

        planner.refresh_captions(queue, paths, ids=["111"])

        assert "so esta" in queue["items"][0]["caption"]
        assert "esta nao pode" not in queue["items"][1]["caption"]


class TestOQueNaoMuda:
    def test_post_publicado_e_intocavel(self, paths):
        """A legenda ja esta no Instagram; reescrever aqui so cria mentira."""
        queue = make_queue(make_item("111", status="published"))
        antes = queue["items"][0]["caption"]
        escrever_legenda(paths, "111", "tentativa de trocar depois do ar")

        resultado = planner.refresh_captions(queue, paths)

        assert queue["items"][0]["caption"] == antes
        assert resultado["ignoradas"][0]["motivo"] == "status published"

    def test_item_em_publicacao_e_intocavel(self, paths):
        """O container ja existe na Meta com o texto antigo."""
        queue = make_queue(make_item("111", status="publishing"))
        antes = queue["items"][0]["caption"]
        escrever_legenda(paths, "111", "trocando no meio do voo")

        planner.refresh_captions(queue, paths)

        assert queue["items"][0]["caption"] == antes

    def test_agendado_fora_do_sistema_e_intocavel(self, paths):
        queue = make_queue(make_item("111", status="scheduled_external"))
        antes = queue["items"][0]["caption"]
        escrever_legenda(paths, "111", "nao e nosso para mexer")

        planner.refresh_captions(queue, paths)

        assert queue["items"][0]["caption"] == antes

    def test_legenda_nao_aprovada_nao_entra(self, paths):
        """O portao de aprovacao vale aqui como vale no plan-queue."""
        queue = make_queue(make_item("111"))
        antes = queue["items"][0]["caption"]
        escrever_legenda(paths, "111", "rascunho ainda cru", status="draft")

        resultado = planner.refresh_captions(queue, paths)

        assert queue["items"][0]["caption"] == antes
        assert resultado["ignoradas"][0]["motivo"] == "legenda nao aprovada"

    def test_sem_arquivo_de_legenda_nao_apaga_o_que_existe(self, paths):
        queue = make_queue(make_item("111"))
        antes = queue["items"][0]["caption"]

        planner.refresh_captions(queue, paths)

        assert queue["items"][0]["caption"] == antes
