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

    def test_existe_uma_hora_de_reserva_depois_do_ultimo_slot(self, janela):
        """A janela nao pode terminar exatamente onde o ultimo post vence.

        Em 15/08/2026 as duas execucoes da hora 0 UTC nao rodaram -- meia-noite
        UTC e o pico de carga do GitHub, que a documentacao deles avisa poder
        descartar jobs agendados. Como a janela acabava ali, o post das 21:26
        ficaria para as 08:13 do dia seguinte: oito horas fora do horario e ainda
        poluindo a medicao do slot da manha.

        Uma hora de folga depois do ultimo vencimento transforma "job descartado"
        em uma hora de atraso.
        """
        horas_utc, _ = janela
        jitter = DEFAULT_CONFIG["jitter_minutes"]
        ultimo = max((int(s["time"][:2]) * 60 + int(s["time"][3:]) + jitter) for s in DEFAULT_SLOTS)
        hora_utc_do_ultimo = ((ultimo // 60) - OFFSET_UTC) % 24
        reserva = (hora_utc_do_ultimo + 1) % 24
        assert reserva in horas_utc, (
            f"o ultimo post vence as {ultimo // 60}h locais ({hora_utc_do_ultimo}h UTC) e "
            f"nao ha execucao em {reserva}h UTC para pegar um job descartado"
        )

    def test_a_madrugada_fica_de_fora(self, janela):
        """Se a janela virar 24h de novo, sao 22 execucoes diarias em fila
        vazia -- e o sinal de que alguem esqueceu de restringir."""
        horas_utc, _ = janela
        cobertas = horas_locais_cobertas(horas_utc)
        assert not (cobertas & {0, 1, 2, 3, 4, 5, 6}), "o cron acorda de madrugada"


class TestOSonoCabeNoJob:
    """O job dorme ate a hora do post; o GitHub mata um job em 6 horas.

    Sao tres numeros em dois arquivos que precisam concordar: o teto de sono, o
    timeout do job e o limite da plataforma. Se o sono passar do timeout, o post
    daquele dia morre sem publicar -- e o log so diz "cancelled", sem apontar o
    motivo. Foi para dar nome a essa falha que este teste existe.
    """

    LIMITE_DO_GITHUB_MIN = 360

    def _do_workflow(self, padrao: str) -> int:
        achado = re.search(padrao, WORKFLOW.read_text(encoding="utf-8"))
        assert achado, f"publish.yml nao tem {padrao!r}"
        return int(achado.group(1))

    def test_o_sono_maximo_cabe_no_timeout_do_job(self):
        sono_min = self._do_workflow(r"MAX_WAIT_SECONDS:\s*(\d+)") / 60
        timeout = self._do_workflow(r"timeout-minutes:\s*(\d+)")
        assert sono_min < timeout, (
            f"o job dorme ate {sono_min:.0f} min mas e cortado em {timeout} min"
        )

    def test_o_timeout_fica_abaixo_do_limite_rigido_do_github(self):
        """Assim o job falha com log proprio em vez de ser morto sem explicacao."""
        assert self._do_workflow(r"timeout-minutes:\s*(\d+)") < self.LIMITE_DO_GITHUB_MIN

    def test_a_espera_cobre_a_folga_entre_execucoes_do_cron(self):
        """De nada adianta dormir se nenhum tique cai dentro da janela de sono.

        Medido em 31/08/2026: o GitHub entregou ~8,5 execucoes por dia, uma a
        cada ~2,8h em media. O teto de sono precisa ser maior que isso com
        folga, senao a maioria dos tiques encerra cedo demais e o post continua
        saindo atrasado.
        """
        sono_h = self._do_workflow(r"MAX_WAIT_SECONDS:\s*(\d+)") / 3600
        assert sono_h >= 4, f"dormir so {sono_h:.1f}h nao cobre a folga real entre execucoes"
