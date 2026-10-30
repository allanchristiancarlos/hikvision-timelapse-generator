#!/usr/bin/env python3
import argparse
import getpass
import html
import shutil
import subprocess
import sys
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
        "--no-audio",
        action="store_true",
        help="Disable audio playback",
    )
    return parser.parse_args()


def build_ffplay_command(rtsp_url: str, username: str = None, password: str = None, transport: str = "tcp", no_audio: bool = False):
    if username and password:
        rtsp_url = with_basic_auth(rtsp_url, username, password)

    command = ["ffplay", "-rtsp_transport", transport]
    if no_audio:
        command.append("-an")
    command.extend(["-fflags", "nobuffer", "-flags", "low_delay", rtsp_url])
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


def main():
    args = parse_args()

    if not shutil.which("ffplay"):
        print("ffplay is not installed or not in PATH. Install ffmpeg to get ffplay.", file=sys.stderr)
        return 2

    # Accept URLs copied from HTML where query delimiters are encoded as &amp;.
    url = html.unescape(args.url.strip())

    username = args.username
    password = args.password
    if username and not password:
        password = getpass.getpass("RTSP password: ")

    command = build_ffplay_command(
        url,
        username=username,
        password=password,
        transport=args.transport,
        no_audio=args.no_audio,
    )

    print("Starting stream playback...")
    print(" ".join(command))
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError:
        print("ffplay command not found.", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"ffplay exited with error: {exc}", file=sys.stderr)
        return exc.returncode if isinstance(exc.returncode, int) else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())