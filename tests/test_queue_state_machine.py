"""The queue's guardrails against publishing the same Reel twice.

Instagram's media_publish is not idempotent, so a duplicate post cannot be
undone. Everything here protects that one invariant.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_item, make_queue, write_queue

from lukasmax_automation import queue as queue_mod
from lukasmax_automation.queue import (
    IllegalTransition,
    QueueError,
    daily_budget_left,
    find_due,
    load_queue,
    migrate_v1,
    save_queue,
    schedule_retry,
    transition,
)


class TestTransitions:
    def test_local_pipeline_runs_end_to_end(self):
        item = make_item(status="planned")
        for target in ("prepared", "hosted", "scheduled"):
            transition(item, target, by="local")
        assert item["status"] == "scheduled"
        assert [entry["to"] for entry in item["history"][-3:]] == [
            "prepared",
            "hosted",
            "scheduled",
        ]

    def test_skipping_a_stage_is_rejected(self):
        item = make_item(status="planned")
        with pytest.raises(IllegalTransition):
            transition(item, "scheduled", by="local")
        assert item["status"] == "planned", "um erro nao pode deixar o item mexido"

    def test_published_is_terminal(self):
        item = make_item(status="publishing")
        transition(item, "published", by="ci")
        for target in queue_mod.STATES:
            with pytest.raises(IllegalTransition):
                transition(item, target, by="ci")

    def test_externally_scheduled_pilot_can_never_be_republished(self):
        """The pilot was scheduled by hand in Business Suite; touching it double-posts."""
        item = make_item(status="scheduled_external")
        for target in ("publishing", "scheduled", "published"):
            with pytest.raises(IllegalTransition):
                transition(item, target, by="ci")

    def test_unknown_state_is_rejected(self):
        with pytest.raises(QueueError):
            transition(make_item(status="planned"), "inventado", by="local")

    def test_history_records_which_side_made_the_change(self):
        item = make_item(status="scheduled")
        transition(item, "publishing", by="ci", note="claim do run 42")
        entry = item["history"][-1]
        assert entry["by"] == "ci"
        assert entry["from"] == "scheduled"
        assert entry["note"] == "claim do run 42"


class TestFindDue:
    def test_only_scheduled_and_elapsed_retries_are_due(self):
        due_now = make_item("due", status="scheduled", minutes_from_now=-5)
        not_yet = make_item("future", status="scheduled", minutes_from_now=60)
        claimed = make_item("claimed", status="publishing", minutes_from_now=-5)
        planned = make_item("planned", status="planned", minutes_from_now=-5)
        hosted = make_item("hosted", status="hosted", minutes_from_now=-5)
        done = make_item("done", status="published", minutes_from_now=-5)
        external = make_item("ext", status="scheduled_external", minutes_from_now=-5)
        queue = make_queue(due_now, not_yet, claimed, planned, hosted, done, external)

        assert [item["id"] for item in find_due(queue)] == [due_now["id"]]

    def test_claimed_item_is_invisible_to_a_concurrent_run(self):
        """The claim is the lock: once publishing, no other run may pick it up."""
        item = make_item(status="scheduled")
        queue = make_queue(item)
        assert find_due(queue) == [item]

        transition(item, "publishing", by="ci")
        assert find_due(queue) == []

    def test_retry_waits_for_its_backoff(self):
        item = make_item(status="scheduled")
        transition(item, "publishing", by="ci")  # so retry only follows a real claim
        schedule_retry(item, "erro 500")
        assert item["status"] == "retry"

        queue = make_queue(item)
        assert find_due(queue) == [], "o backoff ainda nao venceu"

        elapsed = datetime.now(UTC) + timedelta(hours=1)
        assert find_due(queue, elapsed) == [item]

    def test_due_items_come_out_earliest_first(self):
        later = make_item("later", minutes_from_now=-5)
        earlier = make_item("earlier", minutes_from_now=-90)
        assert [item["id"] for item in find_due(make_queue(later, earlier))] == [
            earlier["id"],
            later["id"],
        ]

    def test_naive_timestamp_does_not_crash_the_comparison(self):
        item = make_item(status="scheduled")
        item["scheduled_at"] = "2020-01-01T10:00:00"  # sem offset
        assert find_due(make_queue(item)) == [item]


class TestRetry:
    def test_backoff_grows_then_gives_up(self):
        item = make_item(status="scheduled")
        transition(item, "publishing", by="ci")

        for expected in range(1, queue_mod.MAX_ATTEMPTS):
            schedule_retry(item, "erro transitorio")
            assert item["status"] == "retry"
            assert item["attempts"] == expected
            transition(item, "publishing", by="ci")

        schedule_retry(item, "erro transitorio")
        assert item["status"] == "failed"
        assert item["next_attempt_at"] is None

    def test_failed_can_be_requeued_by_hand(self):
        item = make_item(status="publishing")
        item["attempts"] = queue_mod.MAX_ATTEMPTS - 1
        schedule_retry(item, "acabou")
        assert item["status"] == "failed"
        transition(item, "scheduled", by="local", note="corrigido manualmente")
        assert item["status"] == "scheduled"


class TestDailyLimit:
    def test_budget_shrinks_with_recent_publishes(self, paths):
        log = paths.publish_log
        assert (
            daily_budget_left(log)
            == queue_mod.DAILY_PUBLISH_LIMIT - queue_mod.DAILY_PUBLISH_RESERVE
        )

        queue_mod.append_log(log, {"item_id": "a", "outcome": "published"})
        assert (
            daily_budget_left(log)
            == queue_mod.DAILY_PUBLISH_LIMIT - queue_mod.DAILY_PUBLISH_RESERVE - 1
        )

    def test_budget_reaches_zero_at_the_ceiling(self, paths):
        log = paths.publish_log
        for index in range(queue_mod.DAILY_PUBLISH_LIMIT):
            queue_mod.append_log(log, {"item_id": str(index), "outcome": "published"})
        assert daily_budget_left(log) == 0

    def test_older_than_24h_does_not_count(self, paths):
        log = paths.publish_log
        stale = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            json.dumps({"at": stale, "item_id": "velho", "outcome": "published"}) + "\n",
            encoding="utf-8",
        )
        assert (
            daily_budget_left(log)
            == queue_mod.DAILY_PUBLISH_LIMIT - queue_mod.DAILY_PUBLISH_RESERVE
        )

    def test_failures_do_not_consume_budget(self, paths):
        log = paths.publish_log
        queue_mod.append_log(log, {"item_id": "a", "outcome": "failed"})
        assert (
            daily_budget_left(log)
            == queue_mod.DAILY_PUBLISH_LIMIT - queue_mod.DAILY_PUBLISH_RESERVE
        )

    def test_truncated_log_line_does_not_blind_the_counter(self, paths):
        log = paths.publish_log
        queue_mod.append_log(log, {"item_id": "a", "outcome": "published"})
        with log.open("a", encoding="utf-8") as handle:
            handle.write('{"at": "2026-08-08T10:00:00+00:00", "outcome": "publi')
        assert (
            daily_budget_left(log)
            == queue_mod.DAILY_PUBLISH_LIMIT - queue_mod.DAILY_PUBLISH_RESERVE - 1
        )


class TestStore:
    def test_save_is_atomic_and_round_trips(self, paths):
        queue = make_queue(make_item("123"))
        save_queue(queue, paths.queue)
        assert load_queue(paths.queue) == queue
        assert not list(paths.queue.parent.glob(".queue.json.*")), "sobrou arquivo temporario"

    def test_missing_queue_reads_as_empty(self, paths):
        assert load_queue(paths.queue) == {"version": queue_mod.SCHEMA_VERSION, "items": []}

    def test_v1_queue_refuses_to_load_without_migration(self, paths):
        write_queue(paths.queue, {"items": [{"tiktok_id": "1", "status": "ready"}]})
        with pytest.raises(QueueError, match="migrate-queue"):
            load_queue(paths.queue)


class TestMigration:
    def test_pilot_keeps_its_external_status(self):
        v1 = {
            "items": [
                {
                    "tiktok_id": "7278034913729907974",
                    "source_url": "https://www.tiktok.com/@_lukasmax/video/7278034913729907974",
                    "scheduled_at": "2026-08-12T18:00:00-03:00",
                    "status": "scheduled_external",
                    "caption": "Essa desbloqueou uma memoria",
                    "scheduled_via": "meta_business_suite",
                    "instagram_post_id": "27996092376710770",
                }
            ]
        }
        migrated = migrate_v1(v1)
        item = migrated["items"][0]

        assert migrated["version"] == queue_mod.SCHEMA_VERSION
        assert item["status"] == "scheduled_external"
        assert item["instagram_media_id"] == "27996092376710770"
        assert item["scheduled_via"] == "meta_business_suite"
        assert find_due(migrated) == [], "o piloto nunca pode virar elegivel"

    def test_v1_ready_becomes_scheduled(self):
        v1 = {
            "items": [
                {"tiktok_id": "1", "scheduled_at": "2026-01-01T10:00:00-03:00", "status": "ready"}
            ]
        }
        assert migrate_v1(v1)["items"][0]["status"] == "scheduled"
