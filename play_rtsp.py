#!/usr/bin/env python3
import argparse
import getpass
import html
import os
import shutil
import subprocess
import sys
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
    parser.add_argument("--track", type=int, default=801, help="Hikvision RTSP track id (default: 801)")
    parser.add_argument("--window-seconds", type=int, default=4, help="Clip window used to build start/end times from date/time")
    parser.add_argument("--clip-seconds", type=float, default=2.0, help="How many seconds to download before extracting a frame")
    parser.add_argument("--frame-offset-seconds", type=float, default=1.0, help="Offset inside the downloaded clip for the output frame")
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


def build_ffmpeg_direct_frame_command(rtsp_url: str, output_path: str, username: str = None, password: str = None, transport: str = "tcp"):
    if username and password:
        rtsp_url = with_basic_auth(rtsp_url, username, password)

    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-rtsp_transport",
        transport,
        "-analyzeduration",
        "0",
        "-probesize",
        "32768",
        "-fflags",
        "nobuffer",
        "-i",
        rtsp_url,
        "-an",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-update",
        "1",
        output_path,
    ]


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


def main():
    args = parse_args()

    if not shutil.which("ffmpeg"):
        print("ffmpeg is not installed or not in PATH.", file=sys.stderr)
        return 2

    if args.mode == "play" and not shutil.which("ffplay"):
        print("ffplay is not installed or not in PATH. Install ffmpeg to get ffplay.", file=sys.stderr)
        return 2

    try:
        if args.url:
            # Accept URLs copied from HTML where query delimiters are encoded as &amp;.
            url = html.unescape(args.url.strip())
            capture_dt = None
        else:
            capture_dt = parse_datetime_inputs(args.date, args.time)
            url = build_rtsp_url_from_time(args.host, args.track, capture_dt, args.window_seconds)
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
        if args.output:
            output = Path(args.output)
        elif capture_dt:
            output = Path("./screenshots") / f"frame_{capture_dt.strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
        else:
            output = Path("./screenshots/frame.jpg")
        output.parent.mkdir(parents=True, exist_ok=True)
        direct_frame_command = build_ffmpeg_direct_frame_command(
            url,
            str(output),
            username=username,
            password=password,
            transport=args.transport,
        )
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
        frame_command = build_ffmpeg_extract_frame_command(
            str(tmp_clip),
            str(output),
            frame_offset_seconds=args.frame_offset_seconds,
        )
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

    if mode == "frame":
        print("direct frame:", " ".join(redacted_command(direct_frame_command)))
        print("clip:", " ".join(redacted_command(clip_command)))
        print("clip fallback:", " ".join(redacted_command(clip_command_fallback)))
        print("frame:", " ".join(redacted_command(frame_command)))
    else:
        print(" ".join(redacted_command(command)))
    try:
        if mode == "frame":
            direct_frame_timeout = max(6.0, args.connect_timeout_seconds + 4.0)
            clip_timeout = max(10.0, args.connect_timeout_seconds + args.clip_seconds + 10.0)
            fallback_clip_timeout = max(20.0, args.connect_timeout_seconds + args.clip_seconds + 35.0)
            frame_timeout = 20.0
            try:
                subprocess.run(direct_frame_command, check=True, env=env, timeout=direct_frame_timeout)
                print("Direct frame capture succeeded.")
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                print("Direct frame capture failed, trying short-clip extraction...")
                try:
                    subprocess.run(clip_command, check=True, env=env, timeout=clip_timeout)
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                    if tmp_clip.exists():
                        tmp_clip.unlink()
                    print("Fast clip capture failed, retrying with tolerant probe settings...")
                    subprocess.run(clip_command_fallback, check=True, env=env, timeout=fallback_clip_timeout)
                subprocess.run(frame_command, check=True, env=env, timeout=frame_timeout)
            if tmp_clip.exists():
                tmp_clip.unlink()
        else:
            subprocess.run(command, check=True, env=env)
    except FileNotFoundError:
        print("Required ffmpeg command not found.", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("ffmpeg timed out before finishing. Try a shorter clip or a larger --window-seconds.", file=sys.stderr)
        if mode == "frame" and tmp_clip.exists():
            tmp_clip.unlink()
        return 4
    except subprocess.CalledProcessError as exc:
        print(f"ffmpeg/ffplay exited with return code {exc.returncode}.", file=sys.stderr)
        if mode == "frame" and tmp_clip.exists():
            tmp_clip.unlink()
        return exc.returncode if isinstance(exc.returncode, int) else 1

    if mode == "record":
        print(f"Recording complete: {args.output}")
    if mode == "frame":
        print(f"Frame captured: {output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())