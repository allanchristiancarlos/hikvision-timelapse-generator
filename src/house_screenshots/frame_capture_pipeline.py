import concurrent.futures
import html
import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse, urlunparse

from .runtime import redacted_command, timed_subprocess_run


@dataclass
class FrameExtractionConfig:
    host: str
    track: int
    window_seconds: int
    clip_seconds: float
    frame_offset_seconds: float
    connect_timeout_seconds: float
    transport: str
    delay_seconds: float
    parallel_jobs: int
    username: Optional[str]
    password: Optional[str]


def with_basic_auth(rtsp_url: str, username: str, password: str) -> str:
    parsed = urlparse(rtsp_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid RTSP URL: {rtsp_url}")

    host = parsed.hostname or parsed.netloc
    if parsed.port:
        host = f"{host}:{parsed.port}"

    username_part = quote(username, safe="")
    password_part = quote(password, safe="")
    netloc = f"{username_part}:{password_part}@{host}"
    rebuilt = parsed._replace(netloc=netloc)
    return urlunparse(rebuilt)


def parse_datetime_inputs(date_value: str, time_value: str) -> datetime:
    if not date_value or not time_value:
        raise ValueError("Both --date and --time are required when --url is not provided.")

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_value} {time_value}", fmt)
        except ValueError:
            continue
    raise ValueError("Invalid --date/--time. Use --date YYYY-MM-DD and --time HH:MM or HH:MM:SS.")


def parse_date_value(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid date '{date_str}'. Expected YYYY-MM-DD.") from exc


def parse_date_range(date_value: str):
    if not date_value:
        raise ValueError("--date is required.")

    if ":" not in date_value:
        return [parse_date_value(date_value)]

    parts = date_value.split(":", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Invalid date range. Use YYYY-MM-DD:YYYY-MM-DD.")

    start = parse_date_value(parts[0])
    end = parse_date_value(parts[1])
    if end < start:
        raise ValueError("Invalid date range. End date must be on or after start date.")

    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def build_rtsp_url_from_time(host: str, track: int, start_dt: datetime, window_seconds: int) -> str:
    if not host:
        raise ValueError("--host is required when --url is not provided.")
    if track <= 0:
        raise ValueError("--track must be greater than 0.")
    if window_seconds <= 0:
        raise ValueError("--window-seconds must be greater than 0.")

    start_str = start_dt.strftime("%Y%m%dT%H%M%SZ")
    end_str = (start_dt + timedelta(seconds=window_seconds)).strftime("%Y%m%dT%H%M%SZ")
    return f"rtsp://{host}/Streaming/tracks/{track}/?starttime={start_str}&endtime={end_str}"


def build_capture_times_for_date(target_date: datetime, interval_hours: float):
    if interval_hours <= 0:
        raise ValueError("--interval-hours must be greater than 0.")

    start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
    end = start + timedelta(days=1)
    step = timedelta(seconds=max(1, int(interval_hours * 3600)))

    times = []
    current = start
    while current < end:
        times.append(current)
        current += step
    return times


def build_capture_times_for_count(target_date: datetime, frames_per_day: int):
    if frames_per_day <= 0:
        raise ValueError("--frames-per-day must be greater than 0.")

    start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0)
    step_seconds = 86400.0 / float(frames_per_day)
    times = []
    for i in range(frames_per_day):
        times.append(start + timedelta(seconds=i * step_seconds))
    return times


def load_state(state_path: Path):
    if not state_path.exists():
        return {
            "version": 1,
            "updated_at": None,
            "completed": {},
            "failed": {},
        }

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "version": 1,
            "updated_at": None,
            "completed": {},
            "failed": {},
        }

    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("updated_at", None)
    data.setdefault("completed", {})
    data.setdefault("failed", {})
    if not isinstance(data["completed"], dict):
        data["completed"] = {}
    if not isinstance(data["failed"], dict):
        data["failed"] = {}
    return data


def save_state(state_path: Path, state: dict):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def ts_key(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def resolve_parallel_jobs(requested_jobs: int) -> int:
    if requested_jobs is not None and requested_jobs > 0:
        return requested_jobs

    cpu_count = os.cpu_count() or 1
    return max(1, min(4, cpu_count // 2 if cpu_count > 1 else 1))


def build_ffmpeg_clip_command(rtsp_url: str, output_path: str, username: str = None, password: str = None, transport: str = "tcp", duration_seconds: float = 3.0, relaxed: bool = False):
    if username and password:
        rtsp_url = with_basic_auth(rtsp_url, username, password)

    if relaxed:
        probe_flags = ["-analyzeduration", "3000000", "-probesize", "1048576"]
    else:
        probe_flags = ["-analyzeduration", "0", "-probesize", "32768", "-fflags", "nobuffer"]

    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-rtsp_transport",
        transport,
        *probe_flags,
        "-i",
        rtsp_url,
        "-t",
        str(duration_seconds),
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        "copy",
        "-f",
        "mpegts",
        output_path,
    ]


def build_ffmpeg_extract_frame_command(input_video: str, output_image: str, frame_offset_seconds: float = 1.0):
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-ss",
        str(max(0.0, frame_offset_seconds)),
        "-i",
        input_video,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-update",
        "1",
        output_image,
    ]


def run_frame_capture(url: str, output: Path, config: FrameExtractionConfig, env):
    tmp_clip = output.with_suffix(".tmp.ts")
    if tmp_clip.exists():
        tmp_clip.unlink()

    clip_command = build_ffmpeg_clip_command(
        url,
        str(tmp_clip),
        username=config.username,
        password=config.password,
        transport=config.transport,
        duration_seconds=config.clip_seconds,
        relaxed=True,
    )

    effective_frame_offset = config.frame_offset_seconds
    min_safe_clip = max(0.1, config.clip_seconds)
    if effective_frame_offset >= min_safe_clip:
        print(f"[timing] frame offset {effective_frame_offset:.2f}s exceeds clip length {config.clip_seconds:.2f}s, using 0.0s")
        effective_frame_offset = 0.0

    frame_command = build_ffmpeg_extract_frame_command(
        str(tmp_clip),
        str(output),
        frame_offset_seconds=effective_frame_offset,
    )

    print("clip:", " ".join(redacted_command(clip_command)))
    print("frame:", " ".join(redacted_command(frame_command)))

    clip_timeout = max(10.0, config.connect_timeout_seconds + config.clip_seconds + 4.0)
    frame_timeout = 8.0
    print(f"[timing] configured timeouts -> clip: {clip_timeout:.1f}s, frame extract: {frame_timeout:.1f}s")

    try:
        timed_subprocess_run(
            clip_command,
            check=True,
            env=env,
            timeout=clip_timeout,
            label="clip download",
        )
        timed_subprocess_run(
            frame_command,
            check=True,
            env=env,
            timeout=frame_timeout,
            label="frame extraction",
        )
    finally:
        if tmp_clip.exists():
            tmp_clip.unlink()


def build_capture_plan(date_value: str, frames_per_day: Optional[int], interval_hours: Optional[float], today):
    days = parse_date_range(date_value)
    original_day_count = len(days)
    days = [d for d in days if d.date() <= today]
    skipped_future_days = original_day_count - len(days)
    if skipped_future_days > 0:
        print(f"Ignoring {skipped_future_days} future day(s) after {today.isoformat()}.")
    if not days:
        raise ValueError(f"All requested dates are in the future. Use a date on or before {today.isoformat()}.")

    capture_times = []
    for day_dt in days:
        if frames_per_day is not None:
            capture_times.extend(build_capture_times_for_count(day_dt, frames_per_day))
        else:
            capture_times.extend(build_capture_times_for_date(day_dt, interval_hours))
    return capture_times


def run_batch_capture(config: FrameExtractionConfig, date_value: str, frames_per_day: Optional[int], interval_hours: Optional[float], state_path: Path, output_dir: Path, env):
    now_dt = datetime.now()
    today = now_dt.date()
    capture_times = build_capture_plan(date_value, frames_per_day, interval_hours, today)

    state = load_state(state_path)
    print(f"Resume state file: {state_path}")

    all_items = []
    skipped_existing = 0
    skipped_failed = 0
    retried_failed_today = 0
    skipped_future = 0

    for ts in capture_times:
        output_path = output_dir / f"frame_{ts.strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
        key = ts_key(ts)

        if ts > now_dt:
            skipped_future += 1
            state["failed"].pop(key, None)
            continue

        if output_path.exists():
            skipped_existing += 1
            state["completed"][key] = {
                "output": str(output_path),
                "completed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            state["failed"].pop(key, None)
            continue

        if key in state["failed"]:
            if ts.date() == today:
                retried_failed_today += 1
            else:
                skipped_failed += 1
                continue

        all_items.append((ts, output_path, key))

    if skipped_existing > 0:
        print(f"Skipping {skipped_existing} already extracted frame(s) found on disk.")
    if skipped_future > 0:
        print(f"Skipping {skipped_future} future frame(s) that have not happened yet.")
    if skipped_failed > 0:
        print(f"Skipping {skipped_failed} previously failed frame(s) from state file.")
    if retried_failed_today > 0:
        print(f"Retrying {retried_failed_today} previously failed frame(s) dated today.")

    save_state(state_path, state)

    success = 0
    failures = 0
    total = len(all_items)
    jobs = resolve_parallel_jobs(config.parallel_jobs)

    if total == 0:
        print("Nothing to do: all frames in range are already extracted.")
        return 0

    def _capture_one(index, ts, ts_output):
        ts_output.parent.mkdir(parents=True, exist_ok=True)
        ts_url = build_rtsp_url_from_time(config.host, config.track, ts, config.window_seconds)
        print(f"[{index}/{total}] Capturing {ts.strftime('%Y-%m-%d %H:%M:%S')} -> {ts_output}")
        run_frame_capture(ts_url, ts_output, config, env)
        return index, ts, ts_output

    if jobs <= 1:
        for index, (ts, ts_output, key) in enumerate(all_items, start=1):
            try:
                _, _, ts_output = _capture_one(index, ts, ts_output)
                print(f"[{index}/{total}] Saved {ts_output}")
                state["completed"][key] = {
                    "output": str(ts_output),
                    "completed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                state["failed"].pop(key, None)
                save_state(state_path, state)
                success += 1
            except Exception as exc:
                print(f"[{index}/{total}] Capture failed for {ts.strftime('%Y-%m-%d %H:%M:%S')}")
                state["failed"][key] = {
                    "output": str(ts_output),
                    "error": str(exc),
                    "failed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                save_state(state_path, state)
                failures += 1

            if index < total and config.delay_seconds > 0:
                print(f"[timing] sleeping {config.delay_seconds:.2f}s before next interval")
                time.sleep(config.delay_seconds)
    else:
        future_to_meta = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
            for index, (ts, ts_output, key) in enumerate(all_items, start=1):
                future = executor.submit(_capture_one, index, ts, ts_output)
                future_to_meta[future] = (index, ts, ts_output, key)
                if index < total and config.delay_seconds > 0:
                    time.sleep(config.delay_seconds)

            for future in concurrent.futures.as_completed(future_to_meta):
                index, ts, ts_output, key = future_to_meta[future]
                try:
                    _, _, ts_output = future.result()
                    print(f"[{index}/{total}] Saved {ts_output}")
                    state["completed"][key] = {
                        "output": str(ts_output),
                        "completed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                    state["failed"].pop(key, None)
                    save_state(state_path, state)
                    success += 1
                except Exception as exc:
                    print(f"[{index}/{total}] Capture failed for {ts.strftime('%Y-%m-%d %H:%M:%S')}")
                    state["failed"][key] = {
                        "output": str(ts_output),
                        "error": str(exc),
                        "failed_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                    save_state(state_path, state)
                    failures += 1

    print(f"Batch complete: {success} succeeded, {failures} failed")
    if failures > 0:
        return 5
    return 0


def normalize_url(url: str) -> str:
    return html.unescape(url.strip())


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg"))


def ffplay_available() -> bool:
    return bool(shutil.which("ffplay"))
