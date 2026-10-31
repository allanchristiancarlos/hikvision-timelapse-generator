import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Optional

from .runtime import redacted_command


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


@dataclass
class BackgroundPipelineConfig:
    host: str
    username: str
    password: Optional[str]
    start_date: str
    run_at: str
    every_minutes: Optional[float]
    frames_per_day: int
    track: int
    window_seconds: int
    clip_seconds: float
    connect_timeout_seconds: float
    delay_seconds: float
    parallel_jobs: int
    transport: str
    frames_dir: str
    video_output: str
    video_fps: int
    state_file: str
    scheduler_state: str
    capture_script: str


class BackgroundPipelineApp:
    def __init__(self, config: BackgroundPipelineConfig):
        self.config = config

    def _run_command(self, command, env):
        log("Running: " + " ".join(redacted_command(command)))
        subprocess.run(command, check=True, env=env)

    def run_pipeline(self, env):
        args = self.config
        script_path = Path(args.capture_script)
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

        self._run_command(extract_cmd, env)
        self._run_command(render_cmd, env)

    def run_once(self, env):
        self.run_pipeline(env)
        return 0

    def run_forever(self, env):
        args = self.config
        scheduler_state_path = Path(args.scheduler_state)
        run_at = parse_run_time(args.run_at)

        if args.every_minutes is not None and args.every_minutes <= 0:
            raise ValueError("--every-minutes must be greater than 0")

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
                    self.run_pipeline(env)
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
            time.sleep(min(sleep_seconds, 300.0))
