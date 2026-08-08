"""The bridge from ranking to queue.

This is the step that did not exist: the single queued item had been written by
hand. The eligibility gate is what keeps it honest -- a video reaches the
calendar only when its media is prepared and its caption approved.
"""

from __future__ import annotations

import csv
import json
from datetime import date

import pytest
from conftest import make_item, make_queue

from lukasmax_automation import captions as captions_mod
from lukasmax_automation import planner, scheduling
from lukasmax_automation import queue as queue_mod

MONDAY = date(2026, 8, 10)


def write_ranking(paths, ids: list[str]) -> None:
    paths.ranking_csv.parent.mkdir(parents=True, exist_ok=True)
    with paths.ranking_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["id", "url", "title", "views", "likes", "comments", "shares", "score"],
        )
        writer.writeheader()
        for index, video_id in enumerate(ids):
            writer.writerow(
                {
                    "id": video_id,
                    "url": f"https://www.tiktok.com/@_lukasmax/video/{video_id}",
                    "title": f"video {index}",
                    "views": 1000 - index,
                    "likes": 100,
                    "comments": 10,
                    "shares": 5,
                    "score": 10.0 - index,
                }
            )


def make_ready(paths, video_id: str) -> None:
    paths.ready(video_id).parent.mkdir(parents=True, exist_ok=True)
    paths.ready(video_id).write_bytes(b"video normalizado")


def make_caption(paths, video_id: str, *, status: str = "approved") -> None:
    record = {
        "tiktok_id": video_id,
        "caption": "Uma legenda perfeitamente valida para este teste passar.",
        "hashtags": ["#um", "#dois", "#tres"],
        "alt_text": "descricao",
        "status": status,
        "input_fingerprint": f"sha256:{video_id}",
        "warnings": [],
    }
    captions_mod.save(record, paths.caption(video_id))


def ready_video(paths, video_id: str) -> None:
    make_ready(paths, video_id)
    make_caption(paths, video_id)


class TestEligibilityGate:
    def test_only_fully_prepared_videos_reach_the_calendar(self, paths):
        write_ranking(paths, ["completo", "sem_midia", "sem_legenda", "legenda_rascunho"])
        ready_video(paths, "completo")
        make_caption(paths, "sem_midia")
        make_ready(paths, "sem_legenda")
        make_ready(paths, "legenda_rascunho")
        make_caption(paths, "legenda_rascunho", status="draft")

        result = planner.plan_queue(paths, start=MONDAY, days=7)

        assert [item["tiktok_id"] for item in result["created"]] == ["completo"]
        reasons = {entry.tiktok_id: entry.reason for entry in result["rejected"]}
        assert "midia nao preparada" in reasons["sem_midia"]
        assert "sem legenda" in reasons["sem_legenda"]
        assert "nao aprovada" in reasons["legenda_rascunho"]

    def test_an_empty_caption_is_rejected_even_when_approved(self, paths):
        write_ranking(paths, ["vazia"])
        make_ready(paths, "vazia")
        captions_mod.save(
            {"tiktok_id": "vazia", "caption": "   ", "hashtags": [], "status": "approved"},
            paths.caption("vazia"),
        )

        result = planner.plan_queue(paths, start=MONDAY, days=7)

        assert result["created"] == []
        assert "vazia" in result["rejected"][0].reason or result["rejected"][0].tiktok_id == "vazia"


class TestIdempotence:
    def test_replanning_does_not_duplicate_what_is_already_queued(self, paths):
        write_ranking(paths, ["a", "b"])
        for video_id in ("a", "b"):
            ready_video(paths, video_id)

        first = planner.plan_queue(paths, start=MONDAY, days=7)
        planner.commit_plan(first, paths)

        second = planner.plan_queue(paths, start=MONDAY, days=7)

        assert second["created"] == []
        assert all("ja esta na fila" in entry.reason for entry in second["rejected"])

    def test_the_pilot_slot_is_not_double_booked(self, paths):
        """The externally scheduled pilot occupies a real slot on the calendar."""
        write_ranking(paths, ["a"])
        ready_video(paths, "a")
        pilot = make_item("piloto", status="scheduled_external")
        pilot["scheduled_at"] = "2026-08-12T18:00:00-03:00"
        queue_mod.save_queue(make_queue(pilot), paths.queue)

        result = planner.plan_queue(paths, start=MONDAY, days=7)

        from datetime import datetime

        taken = datetime.fromisoformat(pilot["scheduled_at"])
        for item in result["created"]:
            planned = datetime.fromisoformat(item["scheduled_at"])
            assert abs(planned - taken).total_seconds() >= 240 * 60


class TestCaptionFreezing:
    def test_the_caption_text_is_copied_into_the_queue(self, paths):
        write_ranking(paths, ["a"])
        ready_video(paths, "a")

        result = planner.plan_queue(paths, start=MONDAY, days=7)
        item = result["created"][0]

        assert "Uma legenda perfeitamente valida" in item["caption"]
        assert "#um #dois #tres" in item["caption"]
        assert item["caption_fingerprint"] == "sha256:a"

    def test_editing_the_caption_file_afterwards_does_not_change_the_post(self, paths):
        write_ranking(paths, ["a"])
        ready_video(paths, "a")
        result = planner.plan_queue(paths, start=MONDAY, days=7)
        planner.commit_plan(result, paths)

        record = json.loads(paths.caption("a").read_text(encoding="utf-8"))
        record["caption"] = "TEXTO COMPLETAMENTE DIFERENTE"
        captions_mod.save(record, paths.caption("a"))

        queued = queue_mod.load_queue(paths.queue)["items"][0]
        assert "TEXTO COMPLETAMENTE DIFERENTE" not in queued["caption"]


class TestSafety:
    def test_planning_is_refused_while_an_item_is_being_published(self, paths):
        """Replanning mid-publish could move the calendar under a running post."""
        write_ranking(paths, ["a"])
        ready_video(paths, "a")
        queue_mod.save_queue(make_queue(make_item("x", status="publishing")), paths.queue)

        with pytest.raises(queue_mod.QueueError, match="reconcile"):
            planner.plan_queue(paths, start=MONDAY, days=7)

    def test_an_unknown_strategy_is_rejected(self, paths):
        write_ranking(paths, ["a"])
        with pytest.raises(ValueError, match="Estrategia"):
            planner.plan_queue(paths, start=MONDAY, days=7, strategy="inventada")

    def test_committed_items_land_in_prepared_not_scheduled(self, paths):
        """Nothing becomes publishable until host-media has run."""
        write_ranking(paths, ["a"])
        ready_video(paths, "a")
        result = planner.plan_queue(paths, start=MONDAY, days=7)
        planner.commit_plan(result, paths)

        stored = queue_mod.load_queue(paths.queue)["items"][0]
        assert stored["status"] == "prepared"
        assert queue_mod.find_due(queue_mod.load_queue(paths.queue)) == []


class TestCapacity:
    def test_more_videos_than_slots_leaves_the_rest_for_the_next_window(self, paths):
        ids = [f"v{index}" for index in range(20)]
        write_ranking(paths, ids)
        for video_id in ids:
            ready_video(paths, video_id)

        result = planner.plan_queue(paths, start=MONDAY, days=3, per_day=2)

        assert len(result["created"]) == result["planned_slots"] <= 6
        assert result["unscheduled_eligible"] == 20 - len(result["created"])

    def test_front_loaded_schedules_the_best_videos_first(self, paths):
        ids = [f"v{index}" for index in range(6)]
        write_ranking(paths, ids)
        for video_id in ids:
            ready_video(paths, video_id)

        result = planner.plan_queue(paths, start=MONDAY, days=2, per_day=2)

        assert [item["tiktok_id"] for item in result["created"]][:2] == ["v0", "v1"]

    def test_interleaved_mixes_top_and_middle_of_the_ranking(self, paths):
        ids = [f"v{index}" for index in range(6)]
        write_ranking(paths, ids)
        for video_id in ids:
            ready_video(paths, video_id)

        result = planner.plan_queue(paths, start=MONDAY, days=3, per_day=2, strategy="interleaved")

        scheduled = [item["tiktok_id"] for item in result["created"]]
        assert scheduled[0] == "v0"
        assert scheduled[1] == "v3", "a estrategia intercalada nao misturou o meio do ranking"


class TestSlotsConfig:
    def test_the_planner_uses_the_configured_pool(self, paths):
        write_ranking(paths, ["a"])
        ready_video(paths, "a")
        scheduling.save_slots(
            {
                **scheduling.DEFAULT_CONFIG,
                "pool": [
                    {
                        "id": "so-esse",
                        "weekdays": [0, 1, 2, 3, 4, 5, 6],
                        "time": "07:00",
                        "weight": 1.0,
                        "samples": 0,
                    }
                ],
            },
            paths.slots,
        )

        result = planner.plan_queue(paths, start=MONDAY, days=7)

        assert result["created"][0]["slot_id"] == "so-esse"
        assert result["created"][0]["scheduled_at"].split("T")[1].startswith(("06:", "07:"))
