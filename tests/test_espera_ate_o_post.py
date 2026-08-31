"""Esperar a hora certa em vez de torcer para um tique do cron cair nela.

O cron do GitHub pede 30 execucoes por dia e entrega ~8,5. Entre 17 e 30/08/2026
o atraso mediano entre o horario agendado e o publicado foi de 45 minutos, e 13
dos 39 posts sairam mais de uma hora atrasados -- tres deles cairam nas faixas
de pior desempenho (21h, 00h20 e 02h20) justamente porque o tique da hora certa
nunca rodou.

A correcao inverte a dependencia: em vez de precisar de um tique no minuto
exato, o job dorme ate a hora do post. Basta entao UMA execucao dentro de uma
janela de horas, que e o que o GitHub de fato entrega.

Estes testes cobrem a conta que decide quanto dormir. O que ela nunca pode fazer
e dizer "durma" para um item que ``find_due`` nao publicaria -- as duas
elegibilidades tem de ser a mesma, senao o job dorme cinco horas e acorda para
nao fazer nada.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import make_item, make_queue

from lukasmax_automation import queue as queue_mod

AGORA = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def agendado_para(delta: timedelta, **extras) -> dict:
    item = make_item("111", status="scheduled")
    item["scheduled_at"] = (AGORA + delta).isoformat()
    item.update(extras)
    return item


class TestQuantoFaltaParaOProximo:
    def test_item_ja_vencido_manda_publicar_agora(self):
        queue = make_queue(agendado_para(timedelta(minutes=-30)))
        assert queue_mod.seconds_until_due(queue, AGORA) == 0

    def test_item_no_futuro_devolve_a_espera_em_segundos(self):
        queue = make_queue(agendado_para(timedelta(hours=2)))
        assert queue_mod.seconds_until_due(queue, AGORA) == 2 * 3600

    def test_fila_vazia_nao_manda_esperar_nada(self):
        assert queue_mod.seconds_until_due(make_queue(), AGORA) is None

    def test_pega_o_mais_proximo_e_nao_o_primeiro_da_lista(self):
        queue = make_queue(
            agendado_para(timedelta(hours=9)),
            agendado_para(timedelta(hours=3)),
        )
        assert queue_mod.seconds_until_due(queue, AGORA) == 3 * 3600

    def test_alem_do_horizonte_e_o_mesmo_que_nao_ter_nada(self):
        """Dormir 20 horas estoura o limite de 6h de um job do GitHub."""
        queue = make_queue(agendado_para(timedelta(hours=20)))
        assert queue_mod.seconds_until_due(queue, AGORA, horizon=timedelta(hours=5)) is None


class TestNaoDormirPorQuemNaoSeriaPublicado:
    """A conta da espera e ``find_due`` precisam concordar sobre elegibilidade.

    Se divergirem, o job dorme ate a hora de um item que a publicacao vai
    ignorar -- e o post do dia perde a janela em silencio.
    """

    def test_item_publicado_nao_conta(self):
        queue = make_queue(agendado_para(timedelta(hours=2), status="published"))
        assert queue_mod.seconds_until_due(queue, AGORA) is None

    def test_item_agendado_fora_do_sistema_nao_conta(self):
        queue = make_queue(agendado_para(timedelta(hours=2), status="scheduled_external"))
        assert queue_mod.seconds_until_due(queue, AGORA) is None

    def test_retry_conta_pelo_backoff_e_nao_pelo_horario_original(self):
        item = agendado_para(timedelta(minutes=-30), status="retry")
        item["next_attempt_at"] = (AGORA + timedelta(hours=1)).isoformat()
        assert queue_mod.seconds_until_due(make_queue(item), AGORA) == 3600

    def test_todo_item_que_a_espera_aponta_fica_devido_na_hora_marcada(self):
        """A amarra entre as duas funcoes, verificada e nao assumida."""
        queue = make_queue(agendado_para(timedelta(hours=3)))

        espera = queue_mod.seconds_until_due(queue, AGORA)
        assert queue_mod.find_due(queue, AGORA) == []
        assert queue_mod.find_due(queue, AGORA + timedelta(seconds=espera))
