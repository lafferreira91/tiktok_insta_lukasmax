"""A capa do Reel.

O primeiro post saiu com o quadro 0 -- transicao, olho semicerrado -- porque
`thumb_offset` nao era enviado e o padrao da Meta e o primeiro quadro. Estes
testes protegem as duas metades do conserto: escolher um quadro decente, e
garantir que a escolha chega ate a criacao do container.
"""

from __future__ import annotations

import pytest
from conftest import make_item, make_queue

from lukasmax_automation import covers
from lukasmax_automation import publisher as publisher_mod
from lukasmax_automation import queue as queue_mod
from lukasmax_automation.cli import _cover_targets

np = pytest.importorskip("numpy")


def frame(value: float, *, noise: float = 0.0, size: int = 64):
    """Quadro cinza uniforme, com ruido opcional fazendo as vezes de detalhe."""
    base = np.full((size, size), value, dtype=np.uint8)
    if noise:
        rng = np.random.default_rng(0)
        jitter = rng.normal(0, noise, base.shape)
        base = np.clip(base.astype(float) + jitter, 0, 255).astype(np.uint8)
    return base


class TestSharpness:
    def test_a_flat_frame_scores_near_zero(self):
        """Quadro chapado nao tem borda nenhuma -- e o que sobra de um corte."""
        assert covers._sharpness(frame(128)) == pytest.approx(0.0, abs=1e-6)

    def test_detail_scores_higher_than_blur(self):
        detailed = covers._sharpness(frame(128, noise=40))
        blurred = covers._sharpness(frame(128, noise=2))

        assert detailed > blurred


class TestExposure:
    def test_a_well_exposed_frame_is_not_penalised(self):
        assert covers._exposure_penalty(128.0) == 1.0

    def test_a_black_frame_is_penalised(self):
        assert covers._exposure_penalty(5.0) < 0.5

    def test_a_blown_out_frame_is_penalised(self):
        assert covers._exposure_penalty(250.0) < 0.5

    def test_the_penalty_never_reaches_zero(self):
        """Punir ate zero faria um video escuro inteiro empatar em 0 e a escolha
        cair no primeiro da lista -- exatamente o quadro 0 que queremos evitar."""
        assert covers._exposure_penalty(0.0) > 0
        assert covers._exposure_penalty(255.0) > 0

    def test_exposure_can_outrank_raw_sharpness(self):
        """Um quadro estourado e nitido nao e uma boa capa."""
        blown = 100.0 * covers._exposure_penalty(252.0)
        decent = 60.0 * covers._exposure_penalty(130.0)

        assert decent > blown


class TestTargets:
    def test_published_items_are_never_touched(self):
        """Capa de post publicado nao se muda pela API; reabrir seria mentira."""
        queue = make_queue(
            make_item("111", status="published"),
            make_item("222", status="scheduled"),
        )

        picked = {item["tiktok_id"] for item in _cover_targets(queue, None)}

        assert picked == {"222"}

    def test_scheduled_and_prepared_are_both_editable(self):
        queue = make_queue(
            make_item("111", status="prepared"),
            make_item("222", status="scheduled"),
            make_item("333", status="skipped"),
        )

        picked = {item["tiktok_id"] for item in _cover_targets(queue, None)}

        assert picked == {"111", "222"}


class TestPublishUsesTheChosenFrame:
    def test_the_offset_reaches_the_container(self, paths, publisher, monkeypatch):
        monkeypatch.setenv("PUBLISH_ENABLED", "true")
        monkeypatch.setenv("GITHUB_RUN_ID", "run-1")
        item = make_item("111")
        item["media"]["thumb_offset_ms"] = 3633
        queue_mod.save_queue(make_queue(item), paths.queue)

        publisher_mod.publish_due(paths.queue, paths, publisher=publisher)

        call = next(c for c in publisher.calls if c[0] == "create_container_from_url")
        assert call[-1].get("thumb_offset_ms") == 3633

    def test_an_item_without_a_choice_still_publishes(self, paths, publisher, monkeypatch):
        """Nao ter capa escolhida nao pode virar motivo para nao postar."""
        monkeypatch.setenv("PUBLISH_ENABLED", "true")
        monkeypatch.setenv("GITHUB_RUN_ID", "run-1")
        queue_mod.save_queue(make_queue(make_item("111")), paths.queue)

        result = publisher_mod.publish_due(paths.queue, paths, publisher=publisher)

        assert len(result["published"]) == 1


class TestItemRecemPlanejado:
    def test_item_com_media_nula_recebe_capa(self, paths, monkeypatch):
        """Um item recem-planejado tem "media": None, nao ausente.

        setdefault devolvia esse None e o comando morria com AttributeError no
        primeiro dos 167 videos -- depois de o planejamento ja ter sido gravado.
        """
        import argparse

        from lukasmax_automation import cli

        item = make_item("111")
        item["media"] = None
        queue = make_queue(item)
        queue_mod.save_queue(queue, paths.queue)

        monkeypatch.setattr(
            covers, "candidates", lambda *a, **k: [covers.Candidate(1500, 10.0, 120.0, 10.0)]
        )
        monkeypatch.setattr(covers, "export_frame", lambda *a, **k: paths.root / "x.jpg")
        monkeypatch.setattr(covers, "contact_sheet", lambda *a, **k: paths.root / "y.jpg")

        codigo = cli.cmd_pick_covers(
            argparse.Namespace(ids=None, force=False, root=str(paths.root))
        )

        assert codigo == 0
        gravado = queue_mod.load_queue(paths.queue)["items"][0]
        assert gravado["media"]["thumb_offset_ms"] == 1500
