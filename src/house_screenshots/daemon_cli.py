#!/usr/bin/env python3
import argparse
import os

from pathlib import Path

from .pipeline_daemon import BackgroundPipelineApp, BackgroundPipelineConfig
from .runtime import env_float, env_int, env_optional_float, env_str, load_env_file


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run daily frame extraction and incremental video rendering in the background."
    )
    parser.add_argument("--host", default=env_str("RTSP_HOST"), help="NVR host or IP (or RTSP_HOST in .env)")
    parser.add_argument("--username", default=env_str("RTSP_USERNAME"), help="NVR username (or RTSP_USERNAME in .env)")
    parser.add_argument("--password", default=env_str("RTSP_PASSWORD"), help="NVR password (or RTSP_PASSWORD in .env)")
    parser.add_argument("--start-date", default=env_str("PIPELINE_START_DATE", "2026-10-15"), help="Absolute start date in YYYY-MM-DD")
    parser.add_argument("--run-at", default=env_str("PIPELINE_RUN_AT", "00:10"), help="Daily local time to run (HH:MM)")
    parser.add_argument("--every-minutes", type=float, default=env_optional_float("PIPELINE_EVERY_MINUTES"), help="Test mode: run pipeline every N minutes instead of daily schedule")
    parser.add_argument("--frames-per-day", type=int, default=env_int("PIPELINE_FRAMES_PER_DAY", 48), help="Frames per day for extraction")
    parser.add_argument("--track", type=int, default=env_int("RTSP_TRACK", 801), help="Hikvision track ID")
    parser.add_argument("--window-seconds", type=int, default=env_int("PIPELINE_WINDOW_SECONDS", 4), help="RTSP start/end window length")
    parser.add_argument("--clip-seconds", type=float, default=env_float("PIPELINE_CLIP_SECONDS", 1.0), help="Clip length before frame extraction")
    parser.add_argument("--connect-timeout-seconds", type=float, default=env_float("PIPELINE_CONNECT_TIMEOUT_SECONDS", 5.0), help="RTSP timeout seconds")
    parser.add_argument("--delay-seconds", type=float, default=env_float("PIPELINE_DELAY_SECONDS", 1.0), help="Delay between extraction requests")
    parser.add_argument("--parallel-jobs", type=int, default=env_int("PIPELINE_PARALLEL_JOBS", 1), help="Extraction parallel jobs")
    parser.add_argument("--transport", choices=["tcp", "udp"], default=env_str("RTSP_TRANSPORT", "tcp"), help="RTSP transport")
    parser.add_argument("--frames-dir", default=env_str("PIPELINE_FRAMES_DIR", "./screenshots"), help="Folder containing frame images")
    parser.add_argument("--video-output", default=env_str("PIPELINE_VIDEO_OUTPUT", "./screenshots/timelapse.mp4"), help="Main timelapse output path")
    parser.add_argument("--video-fps", type=int, default=env_int("PIPELINE_VIDEO_FPS", 24), help="Timelapse FPS")
    parser.add_argument("--state-file", default=env_str("PIPELINE_STATE_FILE", "./screenshots/.extract_state.json"), help="Extraction/render state file")
    parser.add_argument("--scheduler-state", default=env_str("PIPELINE_SCHEDULER_STATE", "./screenshots/.daily_pipeline_state.json"), help="Scheduler state file")
    parser.add_argument("--capture-script", default=env_str("PIPELINE_CAPTURE_SCRIPT", env_str("PIPELINE_PLAY_RTSP_SCRIPT", "./src/capture_and_render.py")), help="Path to capture_and_render.py")
    parser.add_argument("--once", action="store_true", help="Run one cycle now and exit")
    return parser.parse_args()


def main():
    env_file = Path(os.environ.get("DAILY_PIPELINE_ENV_FILE", ".env"))
    load_env_file(env_file)

    args = parse_args()
    env = os.environ.copy()
    env.setdefault("TZ", env.get("PIPELINE_TZ", "Asia/Manila"))

    if not args.host:
        raise ValueError("--host is required (or set RTSP_HOST in .env)")
    if not args.username:
        raise ValueError("--username is required (or set RTSP_USERNAME in .env)")

    if not args.password:
        env_password = env.get("RTSP_PASSWORD")
        if env_password:
            args.password = env_password

    config = BackgroundPipelineConfig(
        host=args.host,
        username=args.username,
        password=args.password,
        start_date=args.start_date,
        run_at=args.run_at,
        every_minutes=args.every_minutes,
        frames_per_day=args.frames_per_day,
        track=args.track,
        window_seconds=args.window_seconds,
        clip_seconds=args.clip_seconds,
        connect_timeout_seconds=args.connect_timeout_seconds,
        delay_seconds=args.delay_seconds,
        parallel_jobs=args.parallel_jobs,
        transport=args.transport,
        frames_dir=args.frames_dir,
        video_output=args.video_output,
        video_fps=args.video_fps,
        state_file=args.state_file,
        scheduler_state=args.scheduler_state,
        capture_script=args.capture_script,
    )

    app = BackgroundPipelineApp(config)
    if args.once:
        return app.run_once(env)
    return app.run_forever(env)


if __name__ == "__main__":
    raise SystemExit(main())
