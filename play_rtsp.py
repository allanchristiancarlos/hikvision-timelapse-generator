#!/usr/bin/env python3
import argparse
import getpass
import html
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse


def parse_args():
    parser = argparse.ArgumentParser(description="Play an RTSP stream with optional basic auth.")
    parser.add_argument(
        "--url",
        default="rtsp://192.168.1.27/Streaming/tracks/201/?starttime=20261007T000000Z&endtime=20261007T000400Z&name=00010000232001703&size=178937872",
        help="RTSP URL to play",
    )
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
        choices=["auto", "play", "record"],
        default="auto",
        help="auto: play when display exists, else record to file",
    )
    parser.add_argument(
        "--output",
        default="./screenshots/rtsp_capture.mp4",
        help="Output file path used in record mode",
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


def main():
    args = parse_args()

    if not shutil.which("ffplay"):
        print("ffplay is not installed or not in PATH. Install ffmpeg to get ffplay.", file=sys.stderr)
        return 2

    if not shutil.which("ffmpeg"):
        print("ffmpeg is not installed or not in PATH.", file=sys.stderr)
        return 2

    # Accept URLs copied from HTML where query delimiters are encoded as &amp;.
    url = html.unescape(args.url.strip())

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
        output = Path(args.output)
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
    else:
        command = build_ffplay_command(
            url,
            username=username,
            password=password,
            transport=args.transport,
            no_audio=no_audio,
        )
        print("Starting stream playback...")

    print(" ".join(redacted_command(command)))
    try:
        subprocess.run(command, check=True, env=env)
    except FileNotFoundError:
        print("Required ffmpeg command not found.", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"ffmpeg/ffplay exited with error: {exc}", file=sys.stderr)
        return exc.returncode if isinstance(exc.returncode, int) else 1

    if mode == "record":
        print(f"Recording complete: {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())