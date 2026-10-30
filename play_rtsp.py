#!/usr/bin/env python3
import argparse
import concurrent.futures
import getpass
import html
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse


def parse_args():
    parser = argparse.ArgumentParser(description="Play, record, or capture a single frame from an RTSP stream.")
    parser.add_argument(
        "--url",
        help="Full RTSP URL (optional when using --host --date --time)",
    )
    parser.add_argument("--host", help="NVR IP or domain, for example 192.168.1.27")
    parser.add_argument("--date", help="Recording date in YYYY-MM-DD")
    parser.add_argument("--time", help="Recording time in HH:MM or HH:MM:SS")
    parser.add_argument("--interval-hours", type=float, help="Batch mode: capture one frame every N hours for the provided --date")
    parser.add_argument("--frames-per-day", type=int, help="Batch mode: capture exactly this many evenly spaced frames across the provided --date")
    parser.add_argument("--delay-seconds", type=float, default=1.0, help="Delay between batch requests to avoid overloading NVR")
    parser.add_argument("--parallel-jobs", type=int, default=0, help="Batch mode concurrency. 0 = auto based on CPU cores (capped for NVR safety)")
    parser.add_argument("--track", type=int, default=801, help="Hikvision RTSP track id (default: 801)")
    parser.add_argument("--window-seconds", type=int, default=4, help="Clip window used to build start/end times from date/time")
    parser.add_argument("--clip-seconds", type=float, default=1.0, help="How many seconds to download before extracting a frame")
    parser.add_argument("--frame-offset-seconds", type=float, default=0.0, help="Offset inside the downloaded clip for the output frame")
    parser.add_argument("--connect-timeout-seconds", type=float, default=5.0, help="RTSP connect/read timeout in seconds")
    parser.add_argument("--username", help="RTSP basic auth username")
    parser.add_argument("--password", help="RTSP basic auth password")
    parser.add_argument(
        "--transport",
        choices=["tcp", "udp"],
        default="tcp",
        help="RTSP transport protocol (default: tcp)",
    )
    parser.add_argument(
        "--with-audio",
        action="store_true",
        help="Try to play/record audio too (disabled by default for compatibility)",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "play", "record", "frame"],
        default="frame",
        help="frame: save one JPG; record: save MP4; play: open live playback; auto: play when display exists, else record",
    )
    parser.add_argument(
        "--output",
        help="Output path. Defaults to a timestamped JPG in frame mode, or MP4 in record mode.",
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=30.0,
        help="Record duration in seconds for record mode",
    )
    return parser.parse_args()


def build_ffplay_command(rtsp_url: str, username: str = None, password: str = None, transport: str = "tcp", no_audio: bool = True):
    if username and password:
        rtsp_url = with_basic_auth(rtsp_url, username, password)

    command = ["ffplay", "-rtsp_transport", transport]
    if no_audio:
        command.append("-an")
    command.extend(["-fflags", "nobuffer", "-flags", "low_delay", rtsp_url])
    return command


def build_ffmpeg_record_command(rtsp_url: str, output_path: str, username: str = None, password: str = None, transport: str = "tcp", no_audio: bool = True, duration_seconds: float = 30.0):
    if username and password:
        rtsp_url = with_basic_auth(rtsp_url, username, password)

    command = ["ffmpeg", "-y", "-rtsp_transport", transport, "-i", rtsp_url]
    if duration_seconds and duration_seconds > 0:
        command.extend(["-t", str(duration_seconds)])
    if no_audio:
        command.append("-an")
    command.extend(["-c:v", "copy", "-movflags", "faststart", output_path])
    return command


def build_ffmpeg_frame_command(rtsp_url: str, output_path: str, username: str = None, password: str = None, transport: str = "tcp", no_audio: bool = True):
    if username and password:
        rtsp_url = with_basic_auth(rtsp_url, username, password)

    command = ["ffmpeg", "-y", "-rtsp_transport", transport, "-i", rtsp_url]
    if no_audio:
        command.append("-an")
    command.extend(["-frames:v", "1", "-q:v", "2", "-update", "1", output_path])
    return command


def build_ffmpeg_clip_command(rtsp_url: str, output_path: str, username: str = None, password: str = None, transport: str = "tcp", duration_seconds: float = 3.0, timeout_seconds: float = 15.0, relaxed: bool = False):
    if username and password:
        rtsp_url = with_basic_auth(rtsp_url, username, password)

    if relaxed:
        probe_flags = ["-analyzeduration", "3000000", "-probesize", "1048576"]
    else:
        probe_flags = ["-analyzeduration", "0", "-probesize", "32768", "-fflags", "nobuffer"]

    command = [
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
    return command


def build_ffmpeg_extract_frame_command(input_video: str, output_image: str, frame_offset_seconds: float = 1.0):
    command = [
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
    return command


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


def has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def redacted_command(command):
    redacted = []
    for token in command:
        if token.startswith("rtsp://") and "@" in token:
            parsed = urlparse(token)
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            safe_url = urlunparse(parsed._replace(netloc=f"***:***@{host}"))
            redacted.append(safe_url)
        else:
            redacted.append(token)
    return redacted


def timed_subprocess_run(command, *, env, timeout, label, check=True):
    started = time.perf_counter()
    print(f"[timing] {label} started")
    try:
        result = subprocess.run(command, check=check, env=env, timeout=timeout)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        elapsed = time.perf_counter() - started
        print(f"[timing] {label} failed after {elapsed:.2f}s")
        raise

    elapsed = time.perf_counter() - started
    print(f"[timing] {label} completed in {elapsed:.2f}s")
    return result


def parse_datetime_inputs(date_value: str, time_value: str) -> datetime:
    if not date_value or not time_value:
        raise ValueError("Both --date and --time are required when --url is not provided.")

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date_value} {time_value}", fmt)
        except ValueError:
            continue
    raise ValueError("Invalid --date/--time. Use --date YYYY-MM-DD and --time HH:MM or HH:MM:SS.")


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


def resolve_parallel_jobs(requested_jobs: int) -> int:
    if requested_jobs is not None and requested_jobs > 0:
        return requested_jobs

    cpu_count = os.cpu_count() or 1
    # Conservative default: use half of cores but cap to 4 to avoid overloading NVR.
    return max(1, min(4, cpu_count // 2 if cpu_count > 1 else 1))


def run_frame_capture(url, output, username, password, args, env):
    tmp_clip = output.with_suffix(".tmp.ts")
    if tmp_clip.exists():
        tmp_clip.unlink()

    clip_command = build_ffmpeg_clip_command(
        url,
        str(tmp_clip),
        username=username,
        password=password,
        transport=args.transport,
        duration_seconds=args.clip_seconds,
        timeout_seconds=args.connect_timeout_seconds,
        relaxed=False,
    )
    clip_command_fallback = build_ffmpeg_clip_command(
        url,
        str(tmp_clip),
        username=username,
        password=password,
        transport=args.transport,
        duration_seconds=args.clip_seconds,
        timeout_seconds=args.connect_timeout_seconds,
        relaxed=True,
    )

    effective_frame_offset = args.frame_offset_seconds
    min_safe_clip = max(0.1, args.clip_seconds)
    if effective_frame_offset >= min_safe_clip:
        print(f"[timing] frame offset {effective_frame_offset:.2f}s exceeds clip length {args.clip_seconds:.2f}s, using 0.0s")
        effective_frame_offset = 0.0

    frame_command = build_ffmpeg_extract_frame_command(
        str(tmp_clip),
        str(output),
        frame_offset_seconds=effective_frame_offset,
    )

    print("clip:", " ".join(redacted_command(clip_command)))
    print("clip fallback:", " ".join(redacted_command(clip_command_fallback)))
    print("frame:", " ".join(redacted_command(frame_command)))

    clip_timeout = max(4.0, args.connect_timeout_seconds + args.clip_seconds - 1.0)
    fallback_clip_timeout = max(10.0, args.connect_timeout_seconds + args.clip_seconds + 4.0)
    frame_timeout = 8.0
    print(f"[timing] configured timeouts -> clip: {clip_timeout:.1f}s, fallback clip: {fallback_clip_timeout:.1f}s, frame extract: {frame_timeout:.1f}s")

    try:
        try:
            timed_subprocess_run(
                clip_command,
                check=True,
                env=env,
                timeout=clip_timeout,
                label="fast clip download",
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            if tmp_clip.exists():
                tmp_clip.unlink()
            print("Fast clip capture failed, retrying with tolerant probe settings...")
            timed_subprocess_run(
                clip_command_fallback,
                check=True,
                env=env,
                timeout=fallback_clip_timeout,
                label="fallback clip download",
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


def main():
    args = parse_args()

    if args.interval_hours is not None and args.frames_per_day is not None:
        print("Use either --interval-hours or --frames-per-day, not both.", file=sys.stderr)
        return 1

    if not shutil.which("ffmpeg"):
        print("ffmpeg is not installed or not in PATH.", file=sys.stderr)
        return 2

    if args.mode == "play" and not shutil.which("ffplay"):
        print("ffplay is not installed or not in PATH. Install ffmpeg to get ffplay.", file=sys.stderr)
        return 2

    batch_mode = args.interval_hours is not None or args.frames_per_day is not None
    try:
        if batch_mode:
            if not args.date:
                raise ValueError("--date is required when using --interval-hours or --frames-per-day.")
            if not args.host:
                raise ValueError("--host is required in batch mode.")
            day_dt = datetime.strptime(args.date, "%Y-%m-%d")
            if args.frames_per_day is not None:
                capture_times = build_capture_times_for_count(day_dt, args.frames_per_day)
            else:
                capture_times = build_capture_times_for_date(day_dt, args.interval_hours)
            url = None
            capture_dt = None
        elif args.url:
            # Accept URLs copied from HTML where query delimiters are encoded as &amp;.
            url = html.unescape(args.url.strip())
            capture_dt = None
            capture_times = None
        else:
            capture_dt = parse_datetime_inputs(args.date, args.time)
            url = build_rtsp_url_from_time(args.host, args.track, capture_dt, args.window_seconds)
            capture_times = None
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    username = args.username
    password = args.password
    if username and not password:
        password = getpass.getpass("RTSP password: ")

    no_audio = not args.with_audio
    mode = args.mode
    if mode == "auto":
        mode = "play" if has_display() else "record"

    env = os.environ.copy()
    if "XDG_RUNTIME_DIR" not in env:
        env["XDG_RUNTIME_DIR"] = "/tmp/xdg-runtime"
        Path(env["XDG_RUNTIME_DIR"]).mkdir(parents=True, exist_ok=True)

    if mode == "play" and not has_display():
        print("No GUI display detected in container. Use --mode record or run with X forwarding.", file=sys.stderr)
        return 3

    if mode == "record":
        output_value = args.output or "./screenshots/rtsp_capture.mp4"
        output = Path(output_value)
        output.parent.mkdir(parents=True, exist_ok=True)
        command = build_ffmpeg_record_command(
            url,
            str(output),
            username=username,
            password=password,
            transport=args.transport,
            no_audio=no_audio,
            duration_seconds=args.duration_seconds,
        )
        print(f"Recording stream to {output}...")
    elif mode == "frame":
        if batch_mode:
            output = None
            parallel_jobs = resolve_parallel_jobs(args.parallel_jobs)
            if args.frames_per_day is not None:
                print(f"Batch frame mode: {len(capture_times)} evenly spaced captures for {args.date} (parallel jobs: {parallel_jobs}).")
            else:
                print(f"Batch frame mode: {len(capture_times)} captures for {args.date} at {args.interval_hours} hour interval(s) (parallel jobs: {parallel_jobs}).")
        else:
            if args.output:
                output = Path(args.output)
            elif capture_dt:
                output = Path("./screenshots") / f"frame_{capture_dt.strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
            else:
                output = Path("./screenshots/frame.jpg")
            output.parent.mkdir(parents=True, exist_ok=True)
            print(f"Downloading a short clip then extracting one frame to {output}...")
    else:
        command = build_ffplay_command(
            url,
            username=username,
            password=password,
            transport=args.transport,
            no_audio=no_audio,
        )
        print("Starting stream playback...")

    if mode != "frame":
        print(" ".join(redacted_command(command)))
    overall_started = time.perf_counter()
    try:
        if mode == "frame":
            if batch_mode:
                success = 0
                failures = 0
                total = len(capture_times)
                jobs = resolve_parallel_jobs(args.parallel_jobs)

                def _capture_one(index, ts):
                    ts_output = Path("./screenshots") / f"frame_{ts.strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
                    ts_output.parent.mkdir(parents=True, exist_ok=True)
                    ts_url = build_rtsp_url_from_time(args.host, args.track, ts, args.window_seconds)
                    print(f"[{index}/{total}] Capturing {ts.strftime('%Y-%m-%d %H:%M:%S')} -> {ts_output}")
                    run_frame_capture(ts_url, ts_output, username, password, args, env)
                    return index, ts, ts_output

                if jobs <= 1:
                    for index, ts in enumerate(capture_times, start=1):
                        try:
                            _, _, ts_output = _capture_one(index, ts)
                            print(f"[{index}/{total}] Saved {ts_output}")
                            success += 1
                        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                            print(f"[{index}/{total}] Capture failed for {ts.strftime('%Y-%m-%d %H:%M:%S')}")
                            failures += 1

                        if index < total and args.delay_seconds > 0:
                            print(f"[timing] sleeping {args.delay_seconds:.2f}s before next interval")
                            time.sleep(args.delay_seconds)
                else:
                    future_to_meta = {}
                    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
                        for index, ts in enumerate(capture_times, start=1):
                            future = executor.submit(_capture_one, index, ts)
                            future_to_meta[future] = (index, ts)
                            if index < total and args.delay_seconds > 0:
                                # Stagger submissions to reduce burst pressure on NVR.
                                time.sleep(args.delay_seconds)

                        for future in concurrent.futures.as_completed(future_to_meta):
                            index, ts = future_to_meta[future]
                            try:
                                _, _, ts_output = future.result()
                                print(f"[{index}/{total}] Saved {ts_output}")
                                success += 1
                            except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                                print(f"[{index}/{total}] Capture failed for {ts.strftime('%Y-%m-%d %H:%M:%S')}")
                                failures += 1

                print(f"Batch complete: {success} succeeded, {failures} failed")
                if failures > 0:
                    return 5
            else:
                run_frame_capture(url, output, username, password, args, env)
        else:
            timed_subprocess_run(
                command,
                check=True,
                env=env,
                timeout=None,
                label=f"{mode} execution",
            )
    except FileNotFoundError:
        print("Required ffmpeg command not found.", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("ffmpeg timed out before finishing. Try a shorter clip or a larger --window-seconds.", file=sys.stderr)
        return 4
    except subprocess.CalledProcessError as exc:
        print(f"ffmpeg/ffplay exited with return code {exc.returncode}.", file=sys.stderr)
        return exc.returncode if isinstance(exc.returncode, int) else 1

    if mode == "record":
        print(f"Recording complete: {args.output}")
    if mode == "frame" and not batch_mode:
        print(f"Frame captured: {output}")

    overall_elapsed = time.perf_counter() - overall_started
    print(f"[timing] total runtime: {overall_elapsed:.2f}s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())