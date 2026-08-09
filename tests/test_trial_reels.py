"""Reels de teste: um dos dois posts do dia vai so para nao seguidores.

O teste mais importante aqui e o negativo -- que um item **sem** ``trial`` nao
manda a chave. Passar ``trial_params=None`` viraria a string "None" no urlencode
e a Meta rejeitaria o container, o que so apareceria em producao.
"""

from __future__ import annotations

import json

import pytest
from conftest import FakePublisher, make_queue

from lukasmax_automation import planner
from lukasmax_automation import publisher as publisher_mod
from lukasmax_automation import queue as queue_mod
from lukasmax_automation.instagram import InstagramPublisher


def agendado(item_id: str, dia: str, rank: int, **extras) -> dict:
    item = {
        "id": item_id,
        "tiktok_id": item_id,
        "status": "scheduled",
        "rank": rank,
        "scheduled_at": f"{dia}T12:00:00-03:00",
        "slot_id": "wd-lunch",
        "caption": "legenda",
        "media": {"asset_url": f"https://example.com/{item_id}.mp4"},
        "attempts": 0,
        "max_attempts": 3,
        "history": [],
    }
    item.update(extras)
    return item


class TestMarcacao:
    def test_marca_um_por_dia_e_e_o_de_rank_mais_baixo(self):
        queue = make_queue(
            agendado("a", "2026-09-01", rank=2),
            agendado("b", "2026-09-01", rank=9),
            agendado("c", "2026-09-02", rank=4),
            agendado("d", "2026-09-02", rank=3),
        )
        planner.mark_trials(queue, force=True)
        marcados = {i["id"] for i in queue["items"] if i.get("trial")}
        assert marcados == {"b", "c"}

    def test_a_estrategia_e_manual_nada_sobe_sozinho(self):
        queue = make_queue(agendado("a", "2026-09-01", 1), agendado("b", "2026-09-01", 2))
        planner.mark_trials(queue, force=True)
        (teste,) = [i for i in queue["items"] if i.get("trial")]
        assert teste["trial"] == {"graduation_strategy": "MANUAL"}

    def test_dia_com_um_post_so_nao_vira_teste(self):
        """Um dia inteiro sem nada no feed seria pior que um dia sem teste."""
        queue = make_queue(agendado("a", "2026-09-01", 1))
        planner.mark_trials(queue, force=True)
        assert not any(i.get("trial") for i in queue["items"])

    def test_e_idempotente(self):
        queue = make_queue(agendado("a", "2026-09-01", 1), agendado("b", "2026-09-01", 2))
        primeira = planner.mark_trials(queue, force=True)
        segunda = planner.mark_trials(queue, force=True)
        assert primeira["marcados"] == segunda["marcados"]
        assert sum(1 for i in queue["items"] if i.get("trial")) == 1

    def test_clear_remove_tudo(self):
        queue = make_queue(agendado("a", "2026-09-01", 1), agendado("b", "2026-09-01", 2))
        planner.mark_trials(queue, force=True)
        resultado = planner.mark_trials(queue, clear=True)
        assert len(resultado["limpos"]) == 1
        assert not any(i.get("trial") for i in queue["items"])

    def test_nao_toca_em_publicado(self):
        queue = make_queue(
            agendado("a", "2026-09-01", 1, status="published"),
            agendado("b", "2026-09-01", 2, status="published"),
        )
        planner.mark_trials(queue, force=True)
        assert not any(i.get("trial") for i in queue["items"])

    def test_limit_days_marca_so_os_primeiros_dias(self):
        """A liberacao em etapas: um dia primeiro, o resto depois de verificar."""
        queue = make_queue(
            agendado("a", "2026-09-01", 1),
            agendado("b", "2026-09-01", 2),
            agendado("c", "2026-09-02", 3),
            agendado("d", "2026-09-02", 4),
        )
        planner.mark_trials(queue, limit_days=1, force=True)
        assert {i["id"] for i in queue["items"] if i.get("trial")} == {"b"}

    def test_recusa_sem_force_porque_a_conta_nao_tem_a_permissao(self):
        """Testado ao vivo: a Meta recusa 'trial_params' nesta conta com 400.

        Marcar um item aqui significa um post que falha em producao, entao o
        comando precisa recusar ate a permissao existir.
        """
        queue = make_queue(agendado("a", "2026-09-01", 1), agendado("b", "2026-09-01", 2))
        with pytest.raises(queue_mod.QueueError, match="permissao"):
            planner.mark_trials(queue)
        assert not any(i.get("trial") for i in queue["items"])

    def test_clear_alcanca_item_que_falhou(self):
        """Regressao de 09/08/2026.

        O item que falhou ao publicar sai de TRIALABLE mas mantem a marca. Se o
        clear nao o alcancasse, devolve-lo para a fila com 'requeue'
        reintroduziria exatamente a falha que o tirou de la.
        """
        queue = make_queue(
            agendado("a", "2026-09-01", 1, status="failed", trial={"graduation_strategy": "MANUAL"})
        )
        resultado = planner.mark_trials(queue, clear=True)
        assert resultado["limpos"] == ["a"]
        assert "trial" not in queue["items"][0]

    def test_clear_funciona_sem_force(self):
        """Desfazer nunca pode estar bloqueado."""
        queue = make_queue(agendado("a", "2026-09-01", 1), agendado("b", "2026-09-01", 2))
        planner.mark_trials(queue, force=True)
        planner.mark_trials(queue, clear=True)
        assert not any(i.get("trial") for i in queue["items"])

    def test_recusa_com_item_em_publishing(self):
        queue = make_queue(agendado("a", "2026-09-01", 1, status="publishing"))
        with pytest.raises(queue_mod.QueueError, match="publishing"):
            planner.mark_trials(queue, force=True)


class TestEnvioParaAApi:
    def publica(self, item: dict, tmp_path) -> FakePublisher:
        from lukasmax_automation.paths import Paths

        publisher = FakePublisher()
        # publish_item recebe o item ja reivindicado -- o claim e o lock que
        # impede dois runs de publicarem o mesmo Reel.
        queue_mod.transition(item, "publishing", by="ci", claimed_at="2026-09-01T15:00:00+00:00")
        publisher_mod.publish_item(item, publisher, Paths.resolve(tmp_path))
        return publisher

    def kwargs_do_container(self, publisher: FakePublisher) -> dict:
        return next(c[2] for c in publisher.calls if c[0] == "create_container_from_url")

    def test_item_com_trial_manda_os_parametros(self, tmp_path):
        item = agendado("a", "2026-09-01", 1, trial={"graduation_strategy": "MANUAL"})
        kwargs = self.kwargs_do_container(self.publica(item, tmp_path))
        assert kwargs["trial_params"] == {"graduation_strategy": "MANUAL"}

    def test_item_sem_trial_manda_none(self, tmp_path):
        """None e filtrado dentro do cliente; o que nao pode e virar a string 'None'."""
        kwargs = self.kwargs_do_container(self.publica(agendado("a", "2026-09-01", 1), tmp_path))
        assert kwargs["trial_params"] is None


class TestSerializacao:
    """O cliente de verdade, sem rede: o que exatamente vai no corpo do POST."""

    def campos_enviados(self, **kwargs) -> dict:
        client = InstagramPublisher("42", "TOKEN")
        capturado: dict = {}

        def fake_post(path, **fields):
            capturado.update(fields)
            return {"id": "container-1"}

        client._post = fake_post  # type: ignore[method-assign]
        client.create_container_from_url("https://example.com/v.mp4", "oi", **kwargs)
        return capturado

    def test_sem_trial_a_chave_nao_existe(self):
        assert "trial_params" not in self.campos_enviados()

    def test_trial_none_tambem_nao_manda_a_chave(self):
        assert "trial_params" not in self.campos_enviados(trial_params=None)

    def test_trial_vai_como_json(self):
        campos = self.campos_enviados(trial_params={"graduation_strategy": "MANUAL"})
        assert json.loads(campos["trial_params"]) == {"graduation_strategy": "MANUAL"}
