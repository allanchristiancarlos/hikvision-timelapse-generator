from datetime import date

from app import build_capture_times, build_test_jpeg


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


def test_build_test_jpeg_creates_valid_bytes():
    data = build_test_jpeg()

    assert data.startswith(b"\x89PNG")
    assert len(data) > 100
