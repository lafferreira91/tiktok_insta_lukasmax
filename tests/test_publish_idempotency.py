"""The one irreversible mistake: posting the same Reel twice.

Instagram's media_publish has no idempotency key, so every guard against
duplication has to live on our side. These tests exercise the cron running
repeatedly over the same queue, and crashing at each point where a naive retry
would double-post.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import make_item, make_queue

from lukasmax_automation import publisher as publisher_mod
from lukasmax_automation import queue as queue_mod
from lukasmax_automation.instagram import RetriableError


@pytest.fixture(autouse=True)
def publishing_on(monkeypatch):
    monkeypatch.setenv("PUBLISH_ENABLED", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "run-1")


def run(paths, publisher, **kwargs):
    return publisher_mod.publish_due(paths.queue, paths, publisher=publisher, **kwargs)


class TestSingleRun:
    def test_publishes_one_due_item(self, paths, publisher):
        queue_mod.save_queue(make_queue(make_item("111")), paths.queue)

        result = run(paths, publisher)

        assert result["published"] == ["q_" + result["published"][0].split("_", 1)[1]]
        assert publisher.count("publish") == 1
        stored = queue_mod.load_queue(paths.queue)["items"][0]
        assert stored["status"] == "published"
        assert stored["instagram_media_id"]

    def test_max_per_run_caps_the_blast_radius(self, paths, publisher):
        items = [make_item(str(index), minutes_from_now=-60 + index) for index in range(5)]
        queue_mod.save_queue(make_queue(*items), paths.queue)

        result = run(paths, publisher, max_per_run=1)

        assert len(result["published"]) == 1
        assert publisher.count("publish") == 1

    def test_nothing_happens_when_publishing_is_disabled(self, paths, publisher, monkeypatch):
        monkeypatch.setenv("PUBLISH_ENABLED", "false")
        queue_mod.save_queue(make_queue(make_item("111")), paths.queue)

        result = run(paths, publisher)

        assert result["enabled"] is False
        assert publisher.calls == []

    def test_dry_run_touches_nothing(self, paths, publisher):
        queue_mod.save_queue(make_queue(make_item("111")), paths.queue)

        result = run(paths, publisher, dry_run=True)

        assert result["dry_run"] is True
        assert publisher.count("publish") == 0
        assert queue_mod.load_queue(paths.queue)["items"][0]["status"] == "scheduled"


class TestRepeatedRuns:
    def test_two_consecutive_runs_publish_exactly_once(self, paths, publisher):
        """The cron fires every 30 minutes over the same file."""
        queue_mod.save_queue(make_queue(make_item("111")), paths.queue)

        first = run(paths, publisher)
        second = run(paths, publisher)

        assert len(first["published"]) == 1
        assert second["published"] == []
        assert publisher.count("publish") == 1, "publicou duas vezes"
        assert queue_mod.published_last_24h(paths.publish_log) == 1

    def test_ten_runs_still_publish_once(self, paths, publisher):
        queue_mod.save_queue(make_queue(make_item("111")), paths.queue)

        for _ in range(10):
            run(paths, publisher)

        assert publisher.count("publish") == 1

    def test_claim_is_persisted_before_the_container_is_created(self, paths, publisher):
        """If the claim were written after publishing, a crash between the two
        would leave the item ``scheduled`` and the next run would double-post."""
        seen: list[str] = []
        queue_mod.save_queue(make_queue(make_item("111")), paths.queue)

        original = publisher.create_container_from_url

        def spy(*args, **kwargs):
            seen.append(queue_mod.load_queue(paths.queue)["items"][0]["status"])
            return original(*args, **kwargs)

        publisher.create_container_from_url = spy
        run(paths, publisher)

        assert seen == ["publishing"], "o claim precisa estar em disco antes do container"


class TestCrashRecovery:
    def test_crash_before_the_container_retries_cleanly(self, paths, publisher):
        item = make_item("111", status="publishing", claimed_at=queue_mod.now().isoformat())
        item["container_id"] = None
        queue_mod.save_queue(make_queue(item), paths.queue)

        run(paths, publisher)

        stored = queue_mod.load_queue(paths.queue)["items"][0]
        assert stored["status"] == "retry"
        assert publisher.count("publish") == 0, "nada foi postado, nada a reconciliar"

    def test_crash_after_a_successful_publish_is_detected(self, paths, publisher):
        """The dangerous case: the post went out, then the run died. Retrying
        would create a second post, so we cross-check the account's media."""
        claimed = queue_mod.now() - timedelta(hours=1)
        item = make_item("111", status="publishing", claimed_at=claimed.isoformat())
        item["container_id"] = "container-1"
        queue_mod.save_queue(make_queue(item), paths.queue)

        publisher.recent_media = [
            {
                "id": "media-ja-publicado",
                "timestamp": (claimed + timedelta(minutes=2)).isoformat(),
                "permalink": "https://instagram.com/reel/x",
            }
        ]

        run(paths, publisher)

        stored = queue_mod.load_queue(paths.queue)["items"][0]
        assert stored["status"] == "published"
        assert stored["instagram_media_id"] == "media-ja-publicado"
        assert publisher.count("publish") == 0, "republicou um Reel que ja estava no ar"

    def test_stale_claim_with_no_matching_media_retries(self, paths, publisher):
        claimed = queue_mod.now() - timedelta(hours=2)
        item = make_item("111", status="publishing", claimed_at=claimed.isoformat())
        item["container_id"] = "container-1"
        queue_mod.save_queue(make_queue(item), paths.queue)
        publisher.recent_media = [
            {"id": "antigo", "timestamp": (claimed - timedelta(days=1)).isoformat()}
        ]
        publisher.status_code = "EXPIRED"

        run(paths, publisher)

        stored = queue_mod.load_queue(paths.queue)["items"][0]
        assert stored["status"] == "retry"
        assert stored["container_id"] is None, "um retry precisa de container novo"

    def test_finished_container_is_published_not_recreated(self, paths, publisher):
        """Crashed between processing and publish: reuse the container."""
        item = make_item("111", status="publishing", claimed_at=queue_mod.now().isoformat())
        item["container_id"] = "container-1"
        queue_mod.save_queue(make_queue(item), paths.queue)

        run(paths, publisher)

        stored = queue_mod.load_queue(paths.queue)["items"][0]
        assert stored["status"] == "published"
        assert publisher.count("create_container_from_url") == 0, "criou um container a mais"
        assert publisher.count("publish") == 1


class TestFailureHandling:
    def test_transient_error_schedules_a_retry(self, paths, publisher):
        queue_mod.save_queue(make_queue(make_item("111")), paths.queue)

        def boom(container_id):
            raise RetriableError("500 do lado da Meta")

        publisher.publish = boom
        run(paths, publisher)

        stored = queue_mod.load_queue(paths.queue)["items"][0]
        assert stored["status"] == "retry"
        assert stored["attempts"] == 1
        assert stored["next_attempt_at"]

    def test_item_without_media_fails_instead_of_looping(self, paths, publisher):
        item = make_item("111")
        item["media"] = None
        queue_mod.save_queue(make_queue(item), paths.queue)

        run(paths, publisher)

        stored = queue_mod.load_queue(paths.queue)["items"][0]
        assert stored["status"] == "failed"
        assert publisher.count("publish") == 0


class TestDailyLimit:
    def test_run_stops_when_the_24h_budget_is_gone(self, paths, publisher):
        for index in range(queue_mod.DAILY_PUBLISH_LIMIT):
            queue_mod.append_log(paths.publish_log, {"item_id": str(index), "outcome": "published"})
        queue_mod.save_queue(make_queue(make_item("111")), paths.queue)

        result = run(paths, publisher)

        assert "limite local" in result["skipped"]
        assert publisher.count("publish") == 0

    def test_meta_quota_exhaustion_also_stops_the_run(self, paths, publisher):
        queue_mod.save_queue(make_queue(make_item("111")), paths.queue)
        publisher.content_publishing_limit = lambda: {
            "quota_usage": 25,
            "config": {"quota_total": 25},
        }

        result = run(paths, publisher)

        assert "quota da Meta" in result["skipped"]
        assert publisher.count("publish") == 0
