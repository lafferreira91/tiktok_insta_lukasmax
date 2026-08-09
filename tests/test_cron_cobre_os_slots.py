"""A janela do cron precisa cobrir todos os horarios do pool.

Os dois vivem em arquivos diferentes -- o pool em ``scheduling.py``, a janela em
``publish.yml`` -- e nada alem deste teste os obriga a concordar. Se alguem
adicionar um slot as 21h, ou estreitar o cron, o sintoma seria um post que
simplesmente nao sai, sem erro nenhum em lugar nenhum.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lukasmax_automation.scheduling import DEFAULT_CONFIG, DEFAULT_SLOTS

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/publish.yml"

#: O Brasil nao tem horario de verao desde 2019, entao o offset e fixo. Se
#: voltar, esta constante muda e o teste avisa qual janela deixou de cobrir.
OFFSET_UTC = -3


def janela_do_cron() -> tuple[set[int], set[int]]:
    """As horas UTC e os minutos que o cron do publish.yml dispara."""
    texto = WORKFLOW.read_text(encoding="utf-8")
    achado = re.search(r'cron:\s*"([^"]+)"', texto)
    assert achado, "publish.yml nao tem uma linha de cron"
    minuto, hora, *_ = achado.group(1).split()

    minutos = set()
    for parte in minuto.split(","):
        if parte.startswith("*/"):
            minutos.update(range(0, 60, int(parte[2:])))
        elif parte == "*":
            minutos.update(range(60))
        else:
            minutos.add(int(parte))

    horas = set()
    for parte in hora.split(","):
        if parte == "*":
            horas.update(range(24))
        elif "-" in parte:
            inicio, fim = (int(x) for x in parte.split("-"))
            horas.update(range(inicio, fim + 1))
        else:
            horas.add(int(parte))
    return horas, minutos


@pytest.fixture(scope="module")
def janela():
    return janela_do_cron()


def horas_locais_cobertas(horas_utc: set[int]) -> set[int]:
    return {(h + OFFSET_UTC) % 24 for h in horas_utc}


class TestCobertura:
    def test_todo_slot_cai_dentro_da_janela(self, janela):
        horas_utc, _ = janela
        cobertas = horas_locais_cobertas(horas_utc)
        jitter = DEFAULT_CONFIG["jitter_minutes"]

        for slot in DEFAULT_SLOTS:
            hora, minuto = (int(p) for p in slot["time"].split(":"))
            # O jitter pode empurrar o post para a hora anterior ou seguinte.
            for deslocamento in (-jitter, 0, jitter):
                total = hora * 60 + minuto + deslocamento
                assert (total // 60) % 24 in cobertas, (
                    f"{slot['id']} as {slot['time']} (jitter {deslocamento:+d}min) "
                    f"cai fora da janela do cron"
                )

    def test_a_espera_maxima_nao_passa_de_meia_hora(self, janela):
        """Um post pode atrasar, mas nao pode atrasar tanto que mude de faixa."""
        _, minutos = janela
        ordenados = sorted(minutos)
        maior_intervalo = max(
            (b - a for a, b in zip(ordenados, ordenados[1:], strict=False)),
            default=60,
        )
        volta = 60 - ordenados[-1] + ordenados[0]
        assert max(maior_intervalo, volta) <= 30

    def test_o_cron_evita_o_topo_da_hora(self, janela):
        """A propria documentacao do GitHub diz que o pico de carga e no minuto
        zero, e que sob carga alta jobs agendados podem ser descartados."""
        _, minutos = janela
        assert 0 not in minutos

    def test_a_madrugada_fica_de_fora(self, janela):
        """Se a janela virar 24h de novo, sao 22 execucoes diarias em fila
        vazia -- e o sinal de que alguem esqueceu de restringir."""
        horas_utc, _ = janela
        cobertas = horas_locais_cobertas(horas_utc)
        assert not (cobertas & {0, 1, 2, 3, 4, 5, 6}), "o cron acorda de madrugada"
