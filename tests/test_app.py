from datetime import datetime

from house_screenshots.frame_capture_pipeline import (
    build_capture_times_for_count,
    build_capture_times_for_date,
    build_ffmpeg_clip_command,
    build_rtsp_url_from_time,
)
from house_screenshots.pipeline_daemon import build_date_range


def test_build_capture_times_for_date_uses_interval_hours():
    result = build_capture_times_for_date(datetime(2026, 10, 27), interval_hours=2)

    assert len(result) == 12
    assert result[0].strftime("%H:%M") == "00:00"
    assert result[-1].strftime("%H:%M") == "22:00"


def test_build_capture_times_for_count_evenly_spreads_day():
    result = build_capture_times_for_count(datetime(2026, 10, 27), frames_per_day=24)

    assert len(result) == 24
    assert result[0].strftime("%H:%M:%S") == "00:00:00"
    assert result[1].strftime("%H:%M:%S") == "01:00:00"


def test_build_rtsp_url_from_time_contains_window_times():
    url = build_rtsp_url_from_time("example.local", 801, datetime(2026, 10, 31, 1, 2, 3), 4)

    assert url.startswith("rtsp://example.local/Streaming/tracks/801/")
    assert "starttime=20261031T010203Z" in url
    assert "endtime=20261031T010207Z" in url


def test_build_date_range_for_daemon_pipeline():
    result = build_date_range(datetime(2026, 10, 1).date(), datetime(2026, 10, 31).date())

    assert result == "2026-10-01:2026-10-31"


def test_build_ffmpeg_clip_command_includes_transport_and_target_file():
    command = build_ffmpeg_clip_command(
        "rtsp://example.local/stream",
        "/tmp/clip.ts",
        transport="tcp",
        duration_seconds=2.5,
        relaxed=True,
    )

    assert command[0] == "ffmpeg"
    assert "-rtsp_transport" in command
    assert "tcp" in command
    assert "-t" in command
    assert "2.5" in command
    assert "/tmp/clip.ts" in command
