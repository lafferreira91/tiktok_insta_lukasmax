"""Furar a fila com um video especifico, sem desmontar o calendario.

Uma trend no Instagram tem prazo. Quando a musica de um video do acervo vira
trend, esperar ate a data que o ranking sorteou -- as vezes tres meses -- e
perder a onda inteira. O que se quer e simples: esse video sai hoje, e quem ia
sair hoje herda a data dele.

A troca e simetrica de proposito. Adiantar sem trocar deixaria um buraco na
agenda ou empilharia dois posts no mesmo horario, e as duas coisas so
apareceriam no dia.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import make_item, make_queue

from lukasmax_automation import planner
from lukasmax_automation import queue as queue_mod

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def agendado(tiktok_id: str, dias: int, hora: int = 18, *, slot: str = "wd-commute", **extras):
    quando = datetime.now(SAO_PAULO).replace(
        hour=hora, minute=30, second=0, microsecond=0
    ) + timedelta(days=dias)
    item = make_item(tiktok_id, status="scheduled")
    item["scheduled_at"] = quando.isoformat()
    item["scheduled_at_utc"] = quando.astimezone(ZoneInfo("UTC")).isoformat()
    item["slot_id"] = slot
    item.update(extras)
    return item


class TestATroca:
    def test_o_video_escolhido_herda_o_horario_do_proximo(self):
        queue = make_queue(agendado("proximo", 1), agendado("trend", 90))
        antes = queue["items"][0]["scheduled_at"]

        planner.bump(queue, "trend")

        trend = next(i for i in queue["items"] if i["tiktok_id"] == "trend")
        assert trend["scheduled_at"] == antes

    def test_quem_ia_sair_herda_a_data_do_escolhido(self):
        """Sem isto, adiantar abriria um buraco na agenda."""
        queue = make_queue(agendado("proximo", 1), agendado("trend", 90))
        antes = queue["items"][1]["scheduled_at"]

        planner.bump(queue, "trend")

        proximo = next(i for i in queue["items"] if i["tiktok_id"] == "proximo")
        assert proximo["scheduled_at"] == antes

    def test_o_slot_viaja_junto_com_o_horario(self):
        """slot_id e a chave que liga o post a medicao de desempenho.

        Trocar o relogio sem trocar o slot gravaria o post das 18:30 como se
        tivesse saido as 19:15, envenenando em silencio a comparacao entre os
        dois horarios.
        """
        queue = make_queue(
            agendado("proximo", 1, 18, slot="wd-commute"),
            agendado("trend", 90, 19, slot="wd-prime"),
        )

        planner.bump(queue, "trend")

        por_id = {i["tiktok_id"]: i for i in queue["items"]}
        assert por_id["trend"]["slot_id"] == "wd-commute"
        assert por_id["proximo"]["slot_id"] == "wd-prime"

    def test_o_utc_acompanha_o_horario_local(self):
        queue = make_queue(agendado("proximo", 1), agendado("trend", 90))

        planner.bump(queue, "trend")

        for item in queue["items"]:
            local = datetime.fromisoformat(item["scheduled_at"])
            assert datetime.fromisoformat(item["scheduled_at_utc"]) == local

    def test_ninguem_mais_e_remexido(self):
        queue = make_queue(agendado("proximo", 1), agendado("meio", 5), agendado("trend", 90))
        intocado = queue["items"][1]["scheduled_at"]

        planner.bump(queue, "trend")

        assert queue["items"][1]["scheduled_at"] == intocado

    def test_dry_run_nao_muda_a_fila(self):
        queue = make_queue(agendado("proximo", 1), agendado("trend", 90))
        antes = [i["scheduled_at"] for i in queue["items"]]

        resultado = planner.bump(queue, "trend", dry_run=True)

        assert [i["scheduled_at"] for i in queue["items"]] == antes
        assert resultado["trocou_com"] == "proximo"

    def test_da_para_escolher_a_data_de_destino(self):
        queue = make_queue(agendado("proximo", 1), agendado("depois", 2), agendado("trend", 90))
        alvo = datetime.fromisoformat(queue["items"][1]["scheduled_at"]).date()

        planner.bump(queue, "trend", date=alvo)

        trend = next(i for i in queue["items"] if i["tiktok_id"] == "trend")
        assert datetime.fromisoformat(trend["scheduled_at"]).date() == alvo


class TestOQueEleRecusa:
    def test_video_que_nao_existe(self):
        with pytest.raises(queue_mod.QueueError, match="nao esta na fila"):
            planner.bump(make_queue(agendado("proximo", 1)), "fantasma")

    def test_video_ja_publicado_nao_volta(self):
        """Reagendar um publicado e o caminho direto para o post duplicado."""
        queue = make_queue(agendado("proximo", 1), agendado("trend", 90, status="published"))
        with pytest.raises(queue_mod.QueueError, match="published"):
            planner.bump(queue, "trend")

    def test_recusa_com_item_em_publishing(self):
        """Um run pode estar com o container aberto para o item que eu moveria."""
        queue = make_queue(agendado("proximo", 1, status="publishing"), agendado("trend", 90))
        with pytest.raises(queue_mod.QueueError, match="publishing"):
            planner.bump(queue, "trend")

    def test_recusa_quando_o_horario_de_destino_ja_passou(self):
        """Senao o post sai no proximo tique, fora de qualquer horario medido."""
        queue = make_queue(agendado("passado", -1), agendado("trend", 90))
        with pytest.raises(queue_mod.QueueError, match="ja passou"):
            planner.bump(queue, "trend")

    def test_forca_permite_o_horario_vencido(self):
        queue = make_queue(agendado("passado", -1), agendado("trend", 90))
        planner.bump(queue, "trend", force=True)
        trend = next(i for i in queue["items"] if i["tiktok_id"] == "trend")
        assert datetime.fromisoformat(trend["scheduled_at"]) < datetime.now(SAO_PAULO)

    def test_video_que_ja_e_o_proximo_nao_e_erro_nem_troca(self):
        queue = make_queue(agendado("trend", 1), agendado("outro", 90))
        resultado = planner.bump(queue, "trend")
        assert resultado["trocou_com"] is None

    def test_data_de_destino_sem_post_nenhum(self):
        from datetime import date

        queue = make_queue(agendado("proximo", 1), agendado("trend", 90))
        with pytest.raises(queue_mod.QueueError, match="nenhum post"):
            planner.bump(queue, "trend", date=date(2030, 1, 1))
