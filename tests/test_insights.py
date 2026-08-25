"""A coleta de metricas so presta se comparar posts na mesma idade.

Os testes aqui defendem tres coisas, em ordem de importancia: que rodar duas
vezes nao duplica linha (o arquivo e append-only e nao tem outro registro de "ja
coletei"), que um post nao e medido antes da hora, e que um erro de API nao vaza
o token para um arquivo commitado num repositorio publico.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import FakePublisher, make_queue

from lukasmax_automation import insights

AGORA = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def publicado(idade_horas: float, **extras) -> dict:
    """Um item ja publicado, com a idade pedida."""
    quando = AGORA - timedelta(hours=idade_horas)
    item = {
        "id": f"q_{idade_horas}",
        "tiktok_id": "123",
        "status": "published",
        "slot_id": "wd-afternoon",
        "scheduled_at": quando.isoformat(),
        "published_at": quando.isoformat(),
        "instagram_media_id": f"media-{idade_horas}",
        "media": {"duration_seconds": 48.0},
    }
    item.update(extras)
    return item


class TestParse:
    def test_achata_as_duas_formas_da_api(self):
        payload = {
            "data": [
                {"name": "views", "values": [{"value": 512}]},
                {"name": "reach", "total_value": {"value": 276}},
            ]
        }
        assert insights.parse_insights(payload) == {"views": 512, "reach": 276}

    def test_metrica_ausente_fica_ausente_e_nao_vira_zero(self):
        """Zero seria mentira: a Meta devolve conjunto vazio quando nao tem dado."""
        payload = {"data": [{"name": "views", "values": []}]}
        assert insights.parse_insights(payload) == {}

    def test_payload_vazio_nao_levanta(self):
        assert insights.parse_insights({}) == {}


class TestJanelas:
    def test_post_novo_demais_nao_e_devido(self):
        queue = make_queue(publicado(10))
        assert insights.due_for_collection(queue, [], moment=AGORA) == []

    def test_com_30h_e_devido_so_em_h24(self):
        queue = make_queue(publicado(30))
        devidos = insights.due_for_collection(queue, [], moment=AGORA)
        assert [rotulo for _, rotulo in devidos] == ["h24"]

    def test_com_8_dias_e_devido_nas_duas_idades(self):
        queue = make_queue(publicado(24 * 8))
        devidos = insights.due_for_collection(queue, [], moment=AGORA)
        assert sorted(rotulo for _, rotulo in devidos) == ["d7", "h24"]

    def test_ignora_item_que_nao_foi_publicado(self):
        queue = make_queue({**publicado(48), "status": "scheduled"})
        assert insights.due_for_collection(queue, [], moment=AGORA) == []

    def test_ignora_publicado_sem_media_id(self):
        queue = make_queue({**publicado(48), "instagram_media_id": None})
        assert insights.due_for_collection(queue, [], moment=AGORA) == []


class TestColeta:
    def test_grava_a_linha_com_a_idade_real(self, tmp_path):
        csv_path = tmp_path / "insights.csv"
        queue = make_queue(publicado(30))
        resultado = insights.collect(queue, FakePublisher(), csv_path, moment=AGORA)

        assert resultado["coletados"] == 1
        (linha,) = insights.read_snapshots(csv_path)
        assert linha["age_label"] == "h24"
        assert linha["age_hours"] == "30.0"
        assert linha["reach"] == "276"
        assert linha["slot_id"] == "wd-afternoon"

    def test_segunda_execucao_no_mesmo_dia_grava_zero_linhas(self, tmp_path):
        """A chave (media_id, idade) ja gravada e o unico registro de 'ja coletei'."""
        csv_path = tmp_path / "insights.csv"
        queue = make_queue(publicado(30))
        insights.collect(queue, FakePublisher(), csv_path, moment=AGORA)
        segunda = insights.collect(queue, FakePublisher(), csv_path, moment=AGORA)

        assert segunda["coletados"] == 0
        assert len(insights.read_snapshots(csv_path)) == 1

    def test_dry_run_nao_escreve_nada(self, tmp_path):
        csv_path = tmp_path / "insights.csv"
        queue = make_queue(publicado(30))
        resultado = insights.collect(queue, FakePublisher(), csv_path, moment=AGORA, dry_run=True)

        assert resultado["devidos"] == ["q_30:h24"]
        assert not csv_path.exists()

    def test_cabecalho_escrito_uma_vez_so(self, tmp_path):
        csv_path = tmp_path / "insights.csv"
        insights.collect(make_queue(publicado(30)), FakePublisher(), csv_path, moment=AGORA)
        insights.collect(
            make_queue(publicado(30 * 24)),
            FakePublisher(),
            csv_path,
            moment=AGORA + timedelta(days=1),
        )
        assert csv_path.read_text(encoding="utf-8").count("collected_at") == 1

    def test_erro_numa_midia_nao_derruba_as_outras(self, tmp_path):
        csv_path = tmp_path / "insights.csv"
        queue = make_queue(publicado(30), publicado(31))
        publisher = FakePublisher()
        publisher.insights_error = RuntimeError("400: quota")

        resultado = insights.collect(queue, publisher, csv_path, moment=AGORA)

        assert resultado["erros"] == 2
        assert len(insights.read_snapshots(csv_path)) == 2

    def test_a_linha_de_erro_nao_carrega_o_token(self, tmp_path):
        """O repositorio e publico e este CSV e commitado."""
        csv_path = tmp_path / "insights.csv"
        publisher = FakePublisher()
        publisher.insights_error = RuntimeError(
            "GET https://graph.instagram.com/v26.0/x/insights?access_token=SEGREDO -> 400"
        )
        insights.collect(make_queue(publicado(30)), publisher, csv_path, moment=AGORA)

        conteudo = csv_path.read_text(encoding="utf-8")
        assert "SEGREDO" not in conteudo
        assert "RuntimeError" in conteudo


class TestScore:
    def test_alcance_pequeno_nao_vira_score(self):
        assert insights.score({"reach": "3", "total_interactions": "1"}) is None

    def test_alcance_suficiente_vira_interacoes_por_alcance(self):
        assert insights.score({"reach": "200", "total_interactions": "50"}) == pytest.approx(0.25)

    def test_linha_com_erro_nao_vira_score(self):
        assert insights.score({"reach": "200", "total_interactions": "50", "error": "x"}) is None


class TestPerformancePorSlot:
    def linha(self, **extras) -> dict:
        base = {
            "slot_id": "wd-morning",
            "age_label": "d7",
            "age_hours": "168",
            "is_trial": "false",
            "reach": "200",
            "total_interactions": "50",
        }
        base.update(extras)
        return base

    def test_agrupa_por_slot(self):
        rows = [self.linha(), self.linha(slot_id="wd-lunch", total_interactions="20")]
        assert insights.performance_by_slot(rows) == {
            "wd-morning": [pytest.approx(0.25)],
            "wd-lunch": [pytest.approx(0.10)],
        }

    def test_exclui_reels_de_teste_por_padrao(self):
        """Trial so alcanca nao seguidores: misturar envenena o ajuste em silencio."""
        rows = [self.linha(is_trial="true")]
        assert insights.performance_by_slot(rows) == {}
        assert insights.performance_by_slot(rows, include_trials=True) != {}

    def test_ignora_a_idade_errada(self):
        assert insights.performance_by_slot([self.linha(age_label="h24")]) == {}

    def test_ignora_coleta_atrasada_demais(self):
        """Um post colhido com 30 dias nao e uma medida de 7 dias."""
        assert insights.performance_by_slot([self.linha(age_hours="720")]) == {}

    def test_alimenta_tune_weights_de_ponta_a_ponta(self):
        from lukasmax_automation import scheduling

        rows = [
            self.linha(slot_id="wd-commute"),
            self.linha(slot_id="we-evening", total_interactions="10"),
        ]
        ajustado = scheduling.tune_weights(
            scheduling.DEFAULT_CONFIG, insights.performance_by_slot(rows)
        )
        pesos = {slot["id"]: slot["weight"] for slot in ajustado["pool"]}
        assert pesos["wd-commute"] > pesos["we-evening"]
        assert len(ajustado["pool"]) == len(scheduling.DEFAULT_SLOTS)
