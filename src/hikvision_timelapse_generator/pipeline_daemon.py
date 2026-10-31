import base64
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from .runtime import env_str, get_logger, redacted_command


logger = get_logger()


def log(msg: str):
    logger.info(msg)


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


def _as_bool(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_nextcloud_upload_url(base_url: str, username: str, remote_path: str, file_name: str) -> str:
    cleaned_base = base_url.rstrip("/")
    cleaned_remote = str(remote_path or "").strip().replace("\\", "/").strip("/")
    cleaned_file_name = str(file_name).strip("/")

    if cleaned_remote and cleaned_remote.endswith(cleaned_file_name):
        remote_target = cleaned_remote
    elif cleaned_remote:
        remote_target = f"{cleaned_remote}/{cleaned_file_name}"
    else:
        remote_target = cleaned_file_name

    return f"{cleaned_base}/remote.php/dav/files/{quote(username)}/{remote_target.lstrip('/')}"


def is_nextcloud_upload_enabled():
    configured_flag = env_str("NEXTCLOUD_UPLOAD_ENABLED")
    if configured_flag is not None and configured_flag != "":
        return _as_bool(configured_flag)
    return bool(env_str("NEXTCLOUD_URL") and env_str("NEXTCLOUD_USERNAME") and env_str("NEXTCLOUD_PASSWORD"))


def upload_video_to_nextcloud(file_path, *, base_url=None, username=None, password=None, remote_path=None):
    video_path = Path(file_path)
    base_url = base_url or env_str("NEXTCLOUD_URL")
    username = username or env_str("NEXTCLOUD_USERNAME")
    password = password or env_str("NEXTCLOUD_PASSWORD")
    remote_path = remote_path if remote_path is not None else env_str("NEXTCLOUD_REMOTE_PATH")

    if not base_url:
        raise ValueError("NEXTCLOUD_URL is required for uploads")
    if not username:
        raise ValueError("NEXTCLOUD_USERNAME is required for uploads")
    if not password:
        raise ValueError("NEXTCLOUD_PASSWORD is required for uploads")
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found for upload: {video_path}")

    upload_url = build_nextcloud_upload_url(base_url, username, remote_path, video_path.name)
    auth_header = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    content_type = mimetypes.guess_type(video_path.name)[0] or "application/octet-stream"

    request = urllib.request.Request(upload_url, data=video_path.read_bytes(), method="PUT")
    request.add_header("Content-Type", content_type)
    request.add_header("Authorization", f"Basic {auth_header}")

    try:
        log(f"Uploading rendered video to Nextcloud: {upload_url}")
        response = urllib.request.urlopen(request, timeout=30)
        try:
            status = getattr(response, "status", None)
            if status is not None and status >= 400:
                raise RuntimeError(f"Nextcloud upload failed with HTTP status {status}")
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        return upload_url
    except Exception as exc:
        raise RuntimeError(f"Nextcloud upload failed for {video_path}: {exc}") from exc


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

    def _upload_rendered_video_if_configured(self):
        if not is_nextcloud_upload_enabled():
            log("Nextcloud upload disabled; skipping upload step")
            return

        base_output = Path(self.config.video_output)
        video_dir = base_output.parent
        stem = base_output.stem
        candidate_files = []
        for path in sorted(video_dir.glob(f"{stem}*.mp4")):
            name = path.name
            if any(token in name for token in (".append_segment", ".existing", ".merged")):
                continue
            candidate_files.append(path)

        if not candidate_files:
            log(f"Rendered video not found, skipping Nextcloud upload: {base_output}")
            return

        scheduler_state_path = Path(self.config.scheduler_state)
        state = load_json(scheduler_state_path)
        hashes_by_file = state.setdefault("last_uploaded_video_hashes", {})
        changed_files = []

        for rendered_video in candidate_files:
            current_digest = file_sha256(rendered_video)
            last_digest = hashes_by_file.get(str(rendered_video.resolve()))
            if last_digest != current_digest:
                changed_files.append((rendered_video, current_digest))

        if not changed_files:
            log(f"Skipping Nextcloud upload; all rendered videos are unchanged: {[str(path) for path, _ in []]}")
            return

        for rendered_video, current_digest in changed_files:
            upload_video_to_nextcloud(rendered_video)
            hashes_by_file[str(rendered_video.resolve())] = current_digest
            state["last_uploaded_video_sha256"] = current_digest
            state["last_uploaded_video_name"] = rendered_video.name
            state["last_uploaded_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        save_json(scheduler_state_path, state)

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
        self._upload_rendered_video_if_configured()

    def run_once(self, env):
        self.run_pipeline(env)
        return 0

    def _run_and_persist_state(self, scheduler_state_path: Path, env):
        state = load_json(scheduler_state_path)
        try:
            log("Starting daily extraction and video render")
            self.run_pipeline(env)
            state["last_successful_date"] = datetime.now().date().isoformat()
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

        # Always run immediately on daemon startup before resuming schedule checks.
        self._run_and_persist_state(scheduler_state_path, env)

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
                self._run_and_persist_state(scheduler_state_path, env)

            if args.every_minutes is not None:
                nxt = next_interval_run_datetime(datetime.now(), args.every_minutes)
            else:
                nxt = next_run_datetime(datetime.now(), run_at)

            sleep_seconds = max(30.0, (nxt - datetime.now()).total_seconds())
            log(f"Next scheduled run at {nxt.strftime('%Y-%m-%d %H:%M:%S')}")
            time.sleep(min(sleep_seconds, 300.0))
