"""Republicar um video que ja foi ao ar.

Em 16/09/2026 dois posts sairam com a cartela do CapCut no fim e fizeram 356 e
521 views, contra uma mediana de 3.772. Os arquivos foram cortados; os dois
videos merecem uma segunda chance com a versao limpa.

O estado ``published`` e terminal de proposito -- e a trava que impede o mesmo
item de ser publicado duas vezes. Repostar nao pode afrouxar essa trava: cria um
item NOVO, e o antigo continua publicado e intocado. Esses testes existem para
que a diferenca entre "item novo" e "item reaberto" nunca se perca.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import make_item, make_queue

from lukasmax_automation import planner
from lukasmax_automation import queue as queue_mod

SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def publicado(tiktok_id="111", **extras):
    item = make_item(tiktok_id, status="published")
    quando = datetime.now(SAO_PAULO) - timedelta(days=3)
    item["scheduled_at"] = quando.isoformat()
    item["published_at"] = quando.isoformat()
    item["instagram_media_id"] = "media-antiga"
    item["media"] = {"asset_url": "https://exemplo/111.mp4", "duration_seconds": 30.0}
    item["caption"] = "Legenda original ✨\n\n#Tag"
    item.update(extras)
    return item


def pendente(tiktok_id="222", dias=1):
    item = make_item(tiktok_id, status="scheduled")
    quando = datetime.now(SAO_PAULO) + timedelta(days=dias)
    item["scheduled_at"] = quando.isoformat()
    return item


class TestORepost:
    def test_nasce_um_item_novo_e_o_antigo_continua_publicado(self):
        queue = make_queue(publicado(), pendente())

        planner.repost(queue, "111")

        do_video = [i for i in queue["items"] if i["tiktok_id"] == "111"]
        assert len(do_video) == 2
        antigo = next(i for i in do_video if i["status"] == "published")
        assert antigo["instagram_media_id"] == "media-antiga"
        assert antigo["published_at"] is not None

    def test_o_item_novo_nasce_limpo_do_lado_do_instagram(self):
        """Herdar media_id ou permalink faria o reconcile achar que ja foi ao ar."""
        queue = make_queue(publicado(), pendente())

        planner.repost(queue, "111")

        novo = next(
            i for i in queue["items"] if i["tiktok_id"] == "111" and i["status"] != "published"
        )
        assert novo["instagram_media_id"] is None
        assert novo["published_at"] is None
        assert novo["permalink"] is None
        assert novo["container_id"] is None
        assert novo["attempts"] == 0

    def test_o_id_do_item_novo_e_diferente_do_antigo(self):
        """Dois itens com o mesmo id se sobrescreveriam ao gravar a fila."""
        queue = make_queue(publicado(), pendente())
        antes = queue["items"][0]["id"]

        planner.repost(queue, "111")

        ids = [i["id"] for i in queue["items"] if i["tiktok_id"] == "111"]
        assert len(set(ids)) == 2 and antes in ids

    def test_a_midia_e_a_legenda_vem_do_item_publicado(self):
        queue = make_queue(publicado(), pendente())

        planner.repost(queue, "111")

        novo = next(
            i for i in queue["items"] if i["tiktok_id"] == "111" and i["status"] != "published"
        )
        assert novo["media"]["asset_url"] == "https://exemplo/111.mp4"
        assert novo["caption"] == "Legenda original ✨\n\n#Tag"

    def test_entra_como_scheduled_e_na_frente_da_fila(self):
        """Repost e urgente por definicao: e um post que rendeu mal."""
        queue = make_queue(publicado(), pendente(dias=1))

        planner.repost(queue, "111")

        novo = next(
            i for i in queue["items"] if i["tiktok_id"] == "111" and i["status"] != "published"
        )
        pend = next(i for i in queue["items"] if i["tiktok_id"] == "222")
        assert novo["status"] == "scheduled"
        assert novo["scheduled_at"] < pend["scheduled_at"]

    def test_dry_run_nao_mexe_na_fila(self):
        queue = make_queue(publicado(), pendente())
        antes = len(queue["items"])

        resultado = planner.repost(queue, "111", dry_run=True)

        assert len(queue["items"]) == antes
        assert resultado["criado"] is False


class TestOQueEleRecusa:
    def test_video_que_nunca_foi_publicado(self):
        queue = make_queue(pendente("222"))
        with pytest.raises(queue_mod.QueueError, match="nunca foi publicado"):
            planner.repost(queue, "222")

    def test_video_fora_da_fila(self):
        with pytest.raises(queue_mod.QueueError, match="nao esta na fila"):
            planner.repost(make_queue(publicado()), "fantasma")

    def test_recusa_se_ja_existe_repost_pendente(self):
        """Rodar duas vezes nao pode enfileirar o mesmo video tres vezes."""
        queue = make_queue(publicado(), pendente())
        planner.repost(queue, "111")
        with pytest.raises(queue_mod.QueueError, match="ja tem um repost"):
            planner.repost(queue, "111")

    def test_recusa_com_item_em_publishing(self):
        queue = make_queue(publicado(), pendente("222"))
        queue["items"][1]["status"] = "publishing"
        with pytest.raises(queue_mod.QueueError, match="publishing"):
            planner.repost(queue, "111")
