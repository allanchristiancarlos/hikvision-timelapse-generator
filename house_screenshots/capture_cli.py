#!/usr/bin/env python3
import argparse
import getpass
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .runtime import (
    env_float,
    env_int,
    env_optional_float,
    env_str,
    has_display,
    load_env_file,
    redacted_command,
    timed_subprocess_run,
)
from .frame_capture_pipeline import (
    FrameExtractionConfig,
    build_rtsp_url_from_time,
    ffmpeg_available,
    ffplay_available,
    normalize_url,
    parse_datetime_inputs,
    run_batch_capture,
    run_frame_capture,
)
from .timelapse_renderer import render_incremental_video


def parse_args():
    parser = argparse.ArgumentParser(description="Play, record, or capture a single frame from an RTSP stream.")
    parser.add_argument("--url", help="Full RTSP URL (optional when using --host --date --time)")
    parser.add_argument("--host", default=env_str("RTSP_HOST"), help="NVR IP or domain (or RTSP_HOST in .env)")
    parser.add_argument("--date", default=env_str("EXTRACT_DATE"), help="Recording date in YYYY-MM-DD, or range YYYY-MM-DD:YYYY-MM-DD")
    parser.add_argument("--time", default=env_str("EXTRACT_TIME"), help="Recording time in HH:MM or HH:MM:SS")
    parser.add_argument("--render-video", action="store_true", help="Render/append a 30fps video from extracted frame images")
    parser.add_argument("--frames-dir", default=env_str("PIPELINE_FRAMES_DIR", "./screenshots"), help="Folder containing extracted frame_*.jpg files")
    parser.add_argument("--video-output", default=env_str("PIPELINE_VIDEO_OUTPUT", "./screenshots/timelapse.mp4"), help="Rendered output video path")
    parser.add_argument("--video-fps", type=int, default=env_int("PIPELINE_VIDEO_FPS", 24), help="Output video FPS for render-video mode")
    parser.add_argument("--rerender-all", action="store_true", help="Render video from all frames again, ignoring incremental state")
    parser.add_argument("--interval-hours", type=float, default=env_optional_float("EXTRACT_INTERVAL_HOURS"), help="Batch mode: capture one frame every N hours for the provided --date")
    parser.add_argument("--frames-per-day", type=int, default=(env_int("PIPELINE_FRAMES_PER_DAY", 0) or None), help="Batch mode: capture exactly this many evenly spaced frames across the provided --date")
    parser.add_argument("--delay-seconds", type=float, default=env_float("PIPELINE_DELAY_SECONDS", 1.0), help="Delay between batch requests to avoid overloading NVR")
    parser.add_argument("--parallel-jobs", type=int, default=env_int("PIPELINE_PARALLEL_JOBS", 1), help="Batch mode concurrency")
    parser.add_argument("--state-file", default=env_str("PIPELINE_STATE_FILE", "./screenshots/.extract_state.json"), help="Resume state file path for batch mode")
    parser.add_argument("--track", type=int, default=env_int("RTSP_TRACK", 801), help="Hikvision RTSP track id")
    parser.add_argument("--window-seconds", type=int, default=env_int("PIPELINE_WINDOW_SECONDS", 4), help="Clip window used to build start/end times from date/time")
    parser.add_argument("--clip-seconds", type=float, default=env_float("PIPELINE_CLIP_SECONDS", 1.0), help="How many seconds to download before extracting a frame")
    parser.add_argument("--frame-offset-seconds", type=float, default=env_float("EXTRACT_FRAME_OFFSET_SECONDS", 0.0), help="Offset inside the downloaded clip for the output frame")
    parser.add_argument("--connect-timeout-seconds", type=float, default=env_float("PIPELINE_CONNECT_TIMEOUT_SECONDS", 5.0), help="RTSP connect/read timeout in seconds")
    parser.add_argument("--username", default=env_str("RTSP_USERNAME"), help="RTSP basic auth username")
    parser.add_argument("--password", default=env_str("RTSP_PASSWORD"), help="RTSP basic auth password")
    parser.add_argument("--transport", choices=["tcp", "udp"], default=env_str("RTSP_TRANSPORT", "tcp"), help="RTSP transport protocol")
    parser.add_argument("--with-audio", action="store_true", help="Try to play/record audio too")
    parser.add_argument("--mode", choices=["auto", "play", "record", "frame"], default="frame", help="frame: save one JPG; record: save MP4; play: open live playback")
    parser.add_argument("--output", help="Output path")
    parser.add_argument("--duration-seconds", type=float, default=30.0, help="Record duration in seconds for record mode")
    return parser.parse_args()


def build_ffplay_command(rtsp_url: str, transport: str = "tcp", no_audio: bool = True):
    command = ["ffplay", "-rtsp_transport", transport]
    if no_audio:
        command.append("-an")
    command.extend(["-fflags", "nobuffer", "-flags", "low_delay", rtsp_url])
    return command


def build_ffmpeg_record_command(rtsp_url: str, output_path: str, transport: str = "tcp", no_audio: bool = True, duration_seconds: float = 30.0):
    command = ["ffmpeg", "-y", "-rtsp_transport", transport, "-i", rtsp_url]
    if duration_seconds and duration_seconds > 0:
        command.extend(["-t", str(duration_seconds)])
    if no_audio:
        command.append("-an")
    command.extend(["-c:v", "copy", "-movflags", "faststart", output_path])
    return command


def main():
    env_file = Path(os.environ.get("PLAY_RTSP_ENV_FILE", ".env"))
    load_env_file(env_file)

    args = parse_args()
    args.host = args.host or env_str("RTSP_HOST")
    args.username = args.username or env_str("RTSP_USERNAME")
    args.password = args.password or env_str("RTSP_PASSWORD")
    args.date = args.date or env_str("EXTRACT_DATE")
    args.time = args.time or env_str("EXTRACT_TIME")

    today = datetime.now().date()
    now_dt = datetime.now()

    if not args.date:
        start_date = env_str("PIPELINE_START_DATE")
        if start_date:
            args.date = f"{start_date}:{today.isoformat()}"

    if args.interval_hours is not None and args.frames_per_day is not None:
        print("Use either --interval-hours or --frames-per-day, not both.", file=sys.stderr)
        return 1

    if not ffmpeg_available():
        print("ffmpeg is not installed or not in PATH.", file=sys.stderr)
        return 2

    if args.mode == "play" and not ffplay_available():
        print("ffplay is not installed or not in PATH. Install ffmpeg to get ffplay.", file=sys.stderr)
        return 2

    env = os.environ.copy()
    if "XDG_RUNTIME_DIR" not in env:
        env["XDG_RUNTIME_DIR"] = "/tmp/xdg-runtime"
        Path(env["XDG_RUNTIME_DIR"]).mkdir(parents=True, exist_ok=True)

    if args.render_video:
        try:
            return render_incremental_video(
                frames_dir=Path(args.frames_dir),
                output_video=Path(args.video_output),
                fps=args.video_fps,
                state_path=Path(args.state_file),
                rerender_all=args.rerender_all,
                env=env,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    batch_mode = args.interval_hours is not None or args.frames_per_day is not None

    try:
        if batch_mode:
            if not args.date:
                raise ValueError("--date is required when using --interval-hours or --frames-per-day.")
            if not args.host:
                raise ValueError("--host is required in batch mode. Set RTSP_HOST in .env/.env file or pass --host.")
            url = None
            capture_dt = None
        elif args.url:
            url = normalize_url(args.url)
            capture_dt = None
        else:
            capture_dt = parse_datetime_inputs(args.date, args.time)
            if capture_dt > now_dt:
                raise ValueError(f"Requested date/time is in the future. Use a time on or before {now_dt.strftime('%Y-%m-%d %H:%M:%S')}.")
            url = build_rtsp_url_from_time(args.host, args.track, capture_dt, args.window_seconds)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    username = args.username
    password = args.password
    if username and not password:
        password = getpass.getpass("RTSP password: ")

    extraction_config = FrameExtractionConfig(
        host=args.host,
        track=args.track,
        window_seconds=args.window_seconds,
        clip_seconds=args.clip_seconds,
        frame_offset_seconds=args.frame_offset_seconds,
        connect_timeout_seconds=args.connect_timeout_seconds,
        transport=args.transport,
        delay_seconds=args.delay_seconds,
        parallel_jobs=args.parallel_jobs,
        username=username,
        password=password,
    )

    no_audio = not args.with_audio
    mode = args.mode
    if mode == "auto":
        mode = "play" if has_display() else "record"

    if mode == "play" and not has_display():
        print("No GUI display detected in container. Use --mode record or run with X forwarding.", file=sys.stderr)
        return 3

    overall_started = time.perf_counter()
    try:
        if mode == "record":
            output_value = args.output or "./screenshots/rtsp_capture.mp4"
            output = Path(output_value)
            output.parent.mkdir(parents=True, exist_ok=True)
            command = build_ffmpeg_record_command(
                url,
                str(output),
                transport=args.transport,
                no_audio=no_audio,
                duration_seconds=args.duration_seconds,
            )
            print(f"Recording stream to {output}...")
            print(" ".join(redacted_command(command)))
            timed_subprocess_run(command, check=True, env=env, timeout=None, label="record execution")
            print(f"Recording complete: {output}")
        elif mode == "play":
            command = build_ffplay_command(url, transport=args.transport, no_audio=no_audio)
            print("Starting stream playback...")
            print(" ".join(redacted_command(command)))
            timed_subprocess_run(command, check=True, env=env, timeout=None, label="play execution")
        else:
            if batch_mode:
                output_dir = Path("./screenshots")
                output_dir.mkdir(parents=True, exist_ok=True)
                if args.frames_per_day is not None:
                    print(f"Batch frame mode: evenly spaced captures across {args.date}.")
                else:
                    print(f"Batch frame mode: captures across {args.date} every {args.interval_hours} hour(s).")
                return run_batch_capture(
                    config=extraction_config,
                    date_value=args.date,
                    frames_per_day=args.frames_per_day,
                    interval_hours=args.interval_hours,
                    state_path=Path(args.state_file),
                    output_dir=output_dir,
                    env=env,
                )

            if args.output:
                output = Path(args.output)
            elif capture_dt:
                output = Path("./screenshots") / f"frame_{capture_dt.strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
            else:
                output = Path("./screenshots/frame.jpg")
            output.parent.mkdir(parents=True, exist_ok=True)
            print(f"Downloading a short clip then extracting one frame to {output}...")
            run_frame_capture(url, output, extraction_config, env)
            print(f"Frame captured: {output}")
    except FileNotFoundError:
        print("Required ffmpeg command not found.", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("ffmpeg timed out before finishing. Try a shorter clip or a larger --window-seconds.", file=sys.stderr)
        return 4
    except subprocess.CalledProcessError as exc:
        print(f"ffmpeg/ffplay exited with return code {exc.returncode}.", file=sys.stderr)
        return exc.returncode if isinstance(exc.returncode, int) else 1

    overall_elapsed = time.perf_counter() - overall_started
    print(f"[timing] total runtime: {overall_elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
