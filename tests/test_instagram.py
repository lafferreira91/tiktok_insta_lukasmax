from pathlib import Path

from lukasmax_automation.instagram import InstagramPublisher


def test_complete_publish_protocol_without_external_network(tmp_path: Path):
    video = tmp_path / "pilot.mp4"
    video.write_bytes(b"fake-video")
    publisher = InstagramPublisher("user-1", "secret")
    calls = []

    def fake_request(method, url, data=None, headers=None):
        calls.append((method, url, data, headers))
        if url.endswith("/user-1/media"):
            return {"id": "container-1", "uri": "https://upload.test/container-1"}
        if url == "https://upload.test/container-1":
            return {"success": True}
        if "container-1?" in url:
            return {"status_code": "FINISHED"}
        if url.endswith("/user-1/media_publish"):
            return {"id": "instagram-media-1"}
        raise AssertionError(url)

    publisher._request = fake_request
    result = publisher.publish_reel(video, "Legenda de teste")

    assert result == {"id": "instagram-media-1"}
    assert [call[0] for call in calls] == ["POST", "POST", "GET", "POST"]
    assert calls[1][2] == b"fake-video"


def test_account_check_uses_read_only_endpoint():
    publisher = InstagramPublisher("user-1", "secret")
    calls = []

    def fake_request(method, url, data=None, headers=None):
        calls.append((method, url))
        return {"id": "user-1", "username": "_lukasmax", "account_type": "CREATOR"}

    publisher._request = fake_request
    account = publisher.check_account()

    assert account["username"] == "_lukasmax"
    assert calls[0][0] == "GET"
    assert "access_token=secret" in calls[0][1]
