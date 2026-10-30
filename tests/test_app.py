from datetime import date

from app import build_capture_times, build_channel_candidates, build_record_search_xml, build_test_jpeg


def test_build_capture_times_for_single_day():
    result = build_capture_times(date(2026, 10, 27), interval_hours=1)

    assert len(result) == 24
    assert result[0].strftime("%H:%M") == "00:00"
    assert result[-1].strftime("%H:%M") == "23:00"


def test_build_capture_times_uses_custom_interval():
    result = build_capture_times(date(2026, 10, 27), interval_hours=2)

    assert len(result) == 12
    assert result[0].strftime("%H:%M") == "00:00"
    assert result[-1].strftime("%H:%M") == "22:00"


def test_build_channel_candidates_includes_hikvision_numeric_variants():
    result = build_channel_candidates(2)

    assert result == [2, 102]


def test_build_record_search_xml_contains_time_window():
    xml = build_record_search_xml(2, date(2026, 10, 26), date(2026, 10, 26))

    assert "<channelID>2</channelID>" in xml
    assert "<searchType>time</searchType>" in xml


def test_build_test_jpeg_creates_valid_bytes():
    data = build_test_jpeg()

    assert data.startswith(b"\x89PNG")
    assert len(data) > 100


def test_build_ffmpeg_download_command_includes_auth_and_output():
    from app import build_ffmpeg_download_command

    command = build_ffmpeg_download_command(
        "rtsp://example.local/stream",
        "/tmp/example.mp4",
        username="demo",
        password="secret",
        duration_seconds=30,
    )

    assert command[:3] == ["ffmpeg", "-y", "-rtsp_transport"]
    assert "-user" in command
    assert "demo" in command
    assert "-password" in command
    assert "secret" in command
    assert "rtsp://example.local/stream" in command
    assert "/tmp/example.mp4" in command
    assert "-t" in command
    assert "30" in command
