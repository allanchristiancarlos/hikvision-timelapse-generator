import logging
import sys
import pytest
from datetime import datetime
from pathlib import Path

from hikvision_timelapse_generator.frame_capture_pipeline import (
    build_capture_times_for_count,
    build_capture_times_for_date,
    build_ffmpeg_clip_command,
    build_rtsp_url_from_time,
)
from hikvision_timelapse_generator.pipeline_daemon import (
    BackgroundPipelineApp,
    BackgroundPipelineConfig,
    build_date_range,
    upload_video_to_nextcloud,
)
from hikvision_timelapse_generator.runtime import configure_logging, redacted_command


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


def test_redacted_command_masks_password_and_rtsp_credentials():
    command = [
        "ffmpeg",
        "-i",
        "rtsp://alice:super-secret@example.local/stream?token=abc",
        "-f",
        "mpegts",
        "/tmp/clip.ts",
        "--password",
        "super-secret",
    ]

    redacted = redacted_command(command)
    redacted_rendered = " ".join(redacted)

    assert "super-secret" not in redacted_rendered
    assert "alice" not in redacted_rendered
    assert "***" in redacted_rendered


def test_background_pipeline_logs_redact_passwords(capsys, monkeypatch):
    app = BackgroundPipelineApp(
        BackgroundPipelineConfig(
            host="example.local",
            username="alice",
            password="super-secret",
            start_date="2026-10-01",
            run_at="00:10",
            every_minutes=None,
            frames_per_day=48,
            track=801,
            window_seconds=4,
            clip_seconds=1.0,
            connect_timeout_seconds=5.0,
            delay_seconds=0.0,
            parallel_jobs=1,
            transport="tcp",
            frames_dir="./screenshots",
            video_output="./screenshots/timelapse.mp4",
            video_fps=24,
            state_file="./screenshots/.extract_state.json",
            scheduler_state="./screenshots/.daily_pipeline_state.json",
            capture_script="./src/capture_and_render.py",
        )
    )

    def fake_run(command, **kwargs):
        return None

    monkeypatch.setattr("hikvision_timelapse_generator.pipeline_daemon.subprocess.run", fake_run)

    app._run_command([sys.executable, "-m", "echo", "--password", "super-secret"], {"PATH": "/usr/bin"})

    captured = capsys.readouterr()
    assert "super-secret" not in captured.out
    assert "***" in captured.out


def test_daemon_runs_immediately_on_start_and_keeps_schedule(tmp_path, monkeypatch):
    scheduler_state = tmp_path / ".daily_pipeline_state.json"

    app = BackgroundPipelineApp(
        BackgroundPipelineConfig(
            host="example.local",
            username="alice",
            password="super-secret",
            start_date="2026-10-01",
            run_at="23:59",
            every_minutes=None,
            frames_per_day=48,
            track=801,
            window_seconds=4,
            clip_seconds=1.0,
            connect_timeout_seconds=5.0,
            delay_seconds=0.0,
            parallel_jobs=1,
            transport="tcp",
            frames_dir="./screenshots",
            video_output="./screenshots/timelapse.mp4",
            video_fps=24,
            state_file="./screenshots/.extract_state.json",
            scheduler_state=str(scheduler_state),
            capture_script="./src/capture_and_render.py",
        )
    )

    run_calls = {"count": 0}

    def fake_run_pipeline(_env):
        run_calls["count"] += 1

    def stop_after_first_sleep(_seconds):
        raise KeyboardInterrupt()

    monkeypatch.setattr(app, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr("hikvision_timelapse_generator.pipeline_daemon.should_run_today", lambda *_: False)
    monkeypatch.setattr("hikvision_timelapse_generator.pipeline_daemon.time.sleep", stop_after_first_sleep)

    with pytest.raises(KeyboardInterrupt):
        app.run_forever({"PATH": "/usr/bin"})

    state = scheduler_state.read_text(encoding="utf-8")

    assert run_calls["count"] == 1
    assert '"last_status": "success"' in state


def test_configure_logging_emits_colorized_sections(capsys):
    logger = configure_logging(force_color=True)
    logger.info("Frame extraction started")
    logger.warning("Retrying a failed frame")
    logger.error("Capture command failed")

    captured = capsys.readouterr()
    assert "INFO" in captured.out
    assert "WARNING" in captured.out
    assert "ERROR" in captured.out
    assert "\x1b[" in captured.out


def test_pipeline_logger_uses_configured_handler(capsys):
    configure_logging(force_color=False)
    logging.getLogger("house_screenshots").info("Frame 01/12 started")

    captured = capsys.readouterr()
    assert "Frame 01/12 started" in captured.out


def test_nextcloud_upload_uses_env_configured_basic_auth(monkeypatch, tmp_path):
    video_file = tmp_path / "timelapse.mp4"
    video_file.write_bytes(b"video-bytes")

    monkeypatch.setenv("NEXTCLOUD_UPLOAD_ENABLED", "true")
    monkeypatch.setenv("NEXTCLOUD_URL", "http://192.168.1.145:30027")
    monkeypatch.setenv("NEXTCLOUD_USERNAME", "nextcloud_admin")
    monkeypatch.setenv("NEXTCLOUD_PASSWORD", "super-secret")
    monkeypatch.setenv("NEXTCLOUD_REMOTE_PATH", "Projects/CarlosResidenceVlog")

    seen = {}

    class DummyResponse:
        status = 201

    def fake_urlopen(request, timeout=30):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["auth"] = request.headers.get("Authorization")
        seen["content_type"] = request.headers.get("Content-Type")
        seen["body"] = request.data
        return DummyResponse()

    monkeypatch.setattr("hikvision_timelapse_generator.pipeline_daemon.urllib.request.urlopen", fake_urlopen)

    result = upload_video_to_nextcloud(video_file)

    assert result.endswith("/Projects/CarlosResidenceVlog/timelapse.mp4")
    assert seen["method"] == "PUT"
    assert seen["auth"].startswith("Basic ")
    assert b"video-bytes" in seen["body"]
    assert "super-secret" not in str(seen)


def test_daemon_skips_nextcloud_upload_when_video_is_unchanged(tmp_path, monkeypatch):
    scheduler_state = tmp_path / ".daily_pipeline_state.json"
    video_file = tmp_path / "timelapse.mp4"
    video_file.write_bytes(b"video-bytes")

    app = BackgroundPipelineApp(
        BackgroundPipelineConfig(
            host="example.local",
            username="alice",
            password="super-secret",
            start_date="2026-10-01",
            run_at="00:10",
            every_minutes=None,
            frames_per_day=48,
            track=801,
            window_seconds=4,
            clip_seconds=1.0,
            connect_timeout_seconds=5.0,
            delay_seconds=0.0,
            parallel_jobs=1,
            transport="tcp",
            frames_dir="./screenshots",
            video_output=str(video_file),
            video_fps=24,
            state_file="./screenshots/.extract_state.json",
            scheduler_state=str(scheduler_state),
            capture_script="./src/capture_and_render.py",
        )
    )

    monkeypatch.setenv("NEXTCLOUD_UPLOAD_ENABLED", "true")
    monkeypatch.setenv("NEXTCLOUD_URL", "http://192.168.1.145:30027")
    monkeypatch.setenv("NEXTCLOUD_USERNAME", "nextcloud_admin")
    monkeypatch.setenv("NEXTCLOUD_PASSWORD", "super-secret")
    monkeypatch.setenv("NEXTCLOUD_REMOTE_PATH", "Projects/CarlosResidenceVlog")

    calls = {"count": 0}

    def fake_upload(file_path, **kwargs):
        calls["count"] += 1
        return "http://example.com/uploaded" 

    monkeypatch.setattr("hikvision_timelapse_generator.pipeline_daemon.upload_video_to_nextcloud", fake_upload)

    app._upload_rendered_video_if_configured()
    app._upload_rendered_video_if_configured()

    assert calls["count"] == 1
    state = scheduler_state.read_text(encoding="utf-8")
    assert "last_uploaded_video_sha256" in state


def test_dockerfile_runs_daemon_in_foreground_by_default():
    repo_root = Path(__file__).resolve().parents[1]
    dockerfile = (repo_root / "Dockerfile").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["python", "src/run_capture_daemon.py"]' in dockerfile
    assert 'CMD ["--help"]' not in dockerfile
