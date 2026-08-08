"""Mudar de ideia sobre os horarios depois que a fila ja existe.

Trocar o pool em ``data/slots.json`` nao mexe sozinho em quem ja tem horario
gravado -- e isso e intencional, senao editar a configuracao remarcaria posts
sem aviso. Estes testes cobrem o passo explicito que faz a remarcacao, e o que
ele nunca pode tocar.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import make_item, make_queue

from lukasmax_automation import planner, scheduling

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def config(**overrides):
    return {**scheduling.DEFAULT_CONFIG, **overrides}


class TestOPoolPadrao:
    def test_nenhum_horario_depois_das_21h(self):
        """O dono do perfil pediu; e os proprios numeros dele concordam --
        a faixa 21-00 teve as menores medianas fora da madrugada."""
        for slot in scheduling.DEFAULT_SLOTS:
            hora = int(slot["time"].split(":")[0])
            assert hora < 21, f"{slot['id']} as {slot['time']} e tarde demais"

    def test_nenhum_horario_antes_das_8h(self):
        for slot in scheduling.DEFAULT_SLOTS:
            hora = int(slot["time"].split(":")[0])
            assert hora >= 8, f"{slot['id']} as {slot['time']} e cedo demais"

    def test_dias_uteis_e_fim_de_semana_ambos_cobertos(self):
        uteis = [s for s in scheduling.DEFAULT_SLOTS if 0 in s["weekdays"]]
        fds = [s for s in scheduling.DEFAULT_SLOTS if 5 in s["weekdays"]]
        assert len(uteis) >= 2 and len(fds) >= 2


class TestRemarcacao:
    def test_todos_os_pendentes_ganham_horario_novo(self, paths):
        queue = make_queue(*[make_item(str(i), minutes_from_now=60 * i) for i in range(1, 6)])

        resultado = planner.reschedule(queue, config())

        assert len(resultado["redatados"]) == 5

    def test_a_ordem_dos_videos_e_preservada(self, paths):
        """So o relogio muda: quem estava primeiro continua primeiro."""
        queue = make_queue(
            make_item("aaa", minutes_from_now=60),
            make_item("bbb", minutes_from_now=120),
            make_item("ccc", minutes_from_now=180),
        )

        planner.reschedule(queue, config())

        por_horario = sorted(queue["items"], key=lambda i: i["scheduled_at"])
        assert [i["tiktok_id"] for i in por_horario] == ["aaa", "bbb", "ccc"]

    def test_nada_cai_no_passado(self, paths):
        queue = make_queue(*[make_item(str(i)) for i in range(3)])
        agora = datetime.now(SAO_PAULO)

        planner.reschedule(queue, config())

        for item in queue["items"]:
            assert datetime.fromisoformat(item["scheduled_at"]) > agora

    def test_o_horario_publicado_nao_e_reaproveitado(self, paths):
        """Remarcar em cima de um post que ja foi ao ar amontoaria os dois."""
        publicado = make_item("ja-foi", status="published")
        publicado["scheduled_at"] = datetime.now(SAO_PAULO).replace(hour=17, minute=15).isoformat()
        queue = make_queue(publicado, make_item("111"), make_item("222"))
        gap = timedelta(minutes=scheduling.DEFAULT_CONFIG["min_gap_minutes"])

        planner.reschedule(queue, config())

        ocupado = datetime.fromisoformat(publicado["scheduled_at"])
        for item in queue["items"][1:]:
            assert abs(datetime.fromisoformat(item["scheduled_at"]) - ocupado) >= gap


class TestOQueNaoSeToca:
    def test_publicado_mantem_o_horario(self, paths):
        queue = make_queue(make_item("111", status="published"))
        antes = queue["items"][0]["scheduled_at"]

        resultado = planner.reschedule(queue, config())

        assert queue["items"][0]["scheduled_at"] == antes
        assert resultado["redatados"] == []

    def test_item_em_publicacao_mantem_o_horario(self, paths):
        queue = make_queue(make_item("111", status="publishing"))
        antes = queue["items"][0]["scheduled_at"]

        planner.reschedule(queue, config())

        assert queue["items"][0]["scheduled_at"] == antes

    def test_agendado_fora_do_sistema_mantem_o_horario(self, paths):
        queue = make_queue(make_item("111", status="scheduled_external"))
        antes = queue["items"][0]["scheduled_at"]

        planner.reschedule(queue, config())

        assert queue["items"][0]["scheduled_at"] == antes

    def test_fila_sem_pendentes_nao_e_erro(self, paths):
        queue = make_queue(make_item("111", status="published"))

        resultado = planner.reschedule(queue, config())

        assert resultado["redatados"] == []
        assert "nota" in resultado


class TestLimites:
    def test_horarios_insuficientes_viram_erro_e_nao_silencio(self, paths):
        """Deixar itens sem horario calado seria pior: eles nunca postariam."""
        queue = make_queue(*[make_item(str(i)) for i in range(40)])

        with pytest.raises(scheduling.SchedulingError):
            planner.reschedule(queue, config(pool=[scheduling.DEFAULT_SLOTS[0]]), per_day=1)
