import json
from pathlib import Path

from lukasmax_automation.queue import publish_due


def test_queue_is_inert_when_publish_flag_is_disabled(tmp_path: Path, monkeypatch):
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"items": [{"status": "ready"}]}))
    monkeypatch.setenv("PUBLISH_ENABLED", "false")
    assert publish_due(queue, tmp_path / "media") == []
