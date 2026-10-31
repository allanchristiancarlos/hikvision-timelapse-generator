#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path


def load_env_file(path: Path):
    if not path.exists():
        return

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        os.environ.setdefault(key, value)


def env_str(name: str, default=None):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def env_optional_float(name: str):
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


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
    parser.add_argument("--play-rtsp-script", default=env_str("PIPELINE_PLAY_RTSP_SCRIPT", "./play_rtsp.py"), help="Path to play_rtsp.py")
    parser.add_argument("--once", action="store_true", help="Run one cycle now and exit")
    return parser.parse_args()


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def parse_date_str(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_run_time(value: str) -> dt_time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("Invalid --run-at format. Use HH:MM (24-hour).") from exc


def ensure_file_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_json(path: Path, data: dict):
    ensure_file_parent(path)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def build_date_range(start_date: date, end_date: date) -> str:
    return f"{start_date.isoformat()}:{end_date.isoformat()}"


def run_command(command, env):
    log("Running: " + " ".join(command))
    subprocess.run(command, check=True, env=env)


def run_pipeline(args, env):
    script_path = Path(args.play_rtsp_script)
    if not script_path.exists():
        raise FileNotFoundError(f"play_rtsp script not found: {script_path}")

    start_date = parse_date_str(args.start_date)
    end_date = datetime.now().date()
    if start_date > end_date:
        raise ValueError("--start-date cannot be after today")

    date_range = build_date_range(start_date, end_date)
    common = [
        sys.executable,
        str(script_path),
    ]

    extract_cmd = common + [
        "--host",
        args.host,
        "--date",
        date_range,
        "--frames-per-day",
        str(args.frames_per_day),
        "--username",
        args.username,
        "--transport",
        args.transport,
        "--track",
        str(args.track),
        "--window-seconds",
        str(args.window_seconds),
        "--clip-seconds",
        str(args.clip_seconds),
        "--connect-timeout-seconds",
        str(args.connect_timeout_seconds),
        "--delay-seconds",
        str(args.delay_seconds),
        "--parallel-jobs",
        str(args.parallel_jobs),
        "--state-file",
        args.state_file,
    ]

    if args.password:
        extract_cmd.extend(["--password", args.password])

    render_cmd = common + [
        "--render-video",
        "--frames-dir",
        args.frames_dir,
        "--video-output",
        args.video_output,
        "--video-fps",
        str(args.video_fps),
        "--state-file",
        args.state_file,
    ]

    run_command(extract_cmd, env)
    run_command(render_cmd, env)


def next_run_datetime(now: datetime, run_at: dt_time) -> datetime:
    scheduled_today = datetime.combine(now.date(), run_at)
    if now < scheduled_today:
        return scheduled_today
    return scheduled_today + timedelta(days=1)


def next_interval_run_datetime(now: datetime, every_minutes: float) -> datetime:
    return now + timedelta(minutes=every_minutes)


def should_run_today(now: datetime, run_at: dt_time, last_successful_date):
    if now.time() < run_at:
        return False
    return last_successful_date != now.date().isoformat()


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

    scheduler_state_path = Path(args.scheduler_state)
    run_at = parse_run_time(args.run_at)

    if args.every_minutes is not None and args.every_minutes <= 0:
        raise ValueError("--every-minutes must be greater than 0")

    if args.once:
        run_pipeline(args, env)
        return 0

    log("Daily pipeline daemon started")
    if args.every_minutes is not None:
        log(f"Interval schedule: every {args.every_minutes} minute(s)")
    else:
        log(f"Schedule time: {args.run_at} local")

    while True:
        now = datetime.now()
        state = load_json(scheduler_state_path)
        last_successful_date = state.get("last_successful_date")
        last_run_epoch = state.get("last_run_epoch")

        should_run = False
        if args.every_minutes is not None:
            if last_run_epoch is None:
                should_run = True
            else:
                elapsed = time.time() - float(last_run_epoch)
                should_run = elapsed >= (args.every_minutes * 60.0)
        else:
            should_run = should_run_today(now, run_at, last_successful_date)

        if should_run:
            try:
                log("Starting daily extraction and video render")
                run_pipeline(args, env)
                state["last_successful_date"] = now.date().isoformat()
                state["last_run_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                state["last_run_epoch"] = time.time()
                state["last_status"] = "success"
                save_json(scheduler_state_path, state)
                log("Daily run completed successfully")
            except Exception as exc:
                state["last_run_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                state["last_run_epoch"] = time.time()
                state["last_status"] = "failed"
                state["last_error"] = str(exc)
                save_json(scheduler_state_path, state)
                log(f"Daily run failed: {exc}")

        if args.every_minutes is not None:
            nxt = next_interval_run_datetime(datetime.now(), args.every_minutes)
        else:
            nxt = next_run_datetime(datetime.now(), run_at)
        sleep_seconds = max(30.0, (nxt - datetime.now()).total_seconds())
        log(f"Next scheduled run at {nxt.strftime('%Y-%m-%d %H:%M:%S')}")
        # Wake periodically so the process can react to day rollovers and be observable.
        time.sleep(min(sleep_seconds, 300.0))


if __name__ == "__main__":
    raise SystemExit(main())
