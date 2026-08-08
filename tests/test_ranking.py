from lukasmax_automation.ranking import rank_videos
from lukasmax_automation.tiktok import best_unwatermarked_format


def test_ranking_rewards_shares_and_scale():
    videos = [
        {"id": "a", "view_count": 1000, "like_count": 100, "comment_count": 5, "repost_count": 2},
        {"id": "b", "view_count": 1000, "like_count": 100, "comment_count": 5, "repost_count": 40},
    ]
    assert rank_videos(videos)[0].id == "b"


def test_watermarked_format_is_never_selected():
    formats = [
        {"format_id": "download", "format_note": "watermarked", "vcodec": "h264", "height": 1920},
        {"format_id": "clean", "format_note": "", "vcodec": "h265", "height": 1080, "tbr": 1500},
    ]
    assert best_unwatermarked_format(formats) == "clean"

