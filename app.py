#!/usr/bin/env python3
import argparse
import base64
import getpass
import re
import struct
import sys
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path

import requests


def build_capture_times(target_date: date, interval_hours: float = 1.0):
    if interval_hours <= 0:
        raise ValueError("interval_hours must be greater than 0")

    interval_seconds = int(interval_hours * 3600)
    start = datetime.combine(target_date, datetime.min.time())
    current = start
    timestamps = []

    while current.date() == target_date:
        timestamps.append(current)
        current += timedelta(seconds=interval_seconds)

    return timestamps


def build_test_jpeg() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack("!I", len(data))
            + tag
            + data
            + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    width = 64
    height = 64
    raw_lines = []
    for y in range(height):
        raw_line = b"\x00"
        for x in range(width):
            r = (x * 4) % 256
            g = (y * 6) % 256
            b = ((x + y) * 3) % 256
            raw_line += bytes((r, g, b))
        raw_lines.append(raw_line)

    raw_data = b"".join(raw_lines)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw_data))
    png += chunk(b"IEND", b"")
    return png


def login_to_hikvision(host: str, username: str, password: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Accept": "application/xml, text/xml, */*",
        "User-Agent": "house-screenshots/1.0",
    })
    session.verify = False
    session.timeout = 20

    login_url = f"http://{host}/ISAPI/Security/sessionLogin"
    payload = (
        f"<SessionLogin><userName>{username}</userName>"
        f"<password>{password}</password></SessionLogin>"
    )

    try:
        response = session.post(login_url, data=payload, timeout=20)
        text = response.text.lower()
        if response.status_code == 200 and ("successful" in text or "ok" in text or "statuscode" in text):
            return session
    except requests.RequestException:
        pass

    session.auth = (username, password)
    return session


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}'. Expected YYYY-MM-DD.") from exc


def find_snapshot_urls(base_url: str, channel: int):
    urls = []
    for channel_num in [channel, channel]:
        urls.extend([
            f"{base_url}/ISAPI/Streaming/channels/{channel_num}/picture",
            f"{base_url}/ISAPI/VideoInput/channels/{channel_num}/picture",
        ])
    return list(dict.fromkeys(urls))


def capture_snapshot(session: requests.Session, base_url: str, channel: int, at_time: datetime, output_dir: Path):
    capture_time = at_time.strftime("%Y-%m-%dT%H:%M:%S")

    for url in find_snapshot_urls(base_url, channel):
        request_url = f"{url}?cameraTime={capture_time}"
        try:
            response = session.get(request_url, timeout=20, stream=True)
        except requests.RequestException:
            continue

        if response.status_code != 200:
            continue

        content_type = response.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            text = response.text.lower()
            if "image" not in text and "jpg" not in text and "jpeg" not in text:
                continue

        output_path = output_dir / f"{at_time.strftime('%Y-%m-%d_%H-%M')}.jpg"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        return output_path

    return None


def prompt_for_value(question: str, default: str = None):
    prompt = question
    if default:
        prompt += f" [{default}]"
    prompt += ": "
    value = input(prompt).strip()
    return value if value else default


def main():
    parser = argparse.ArgumentParser(description="Capture Hikvision NVR screenshots at fixed intervals for one date.")
    parser.add_argument("--host", default="192.168.1.27", help="NVR IP or hostname")
    parser.add_argument("--date", type=parse_date, help="Date to capture in YYYY-MM-DD format")
    parser.add_argument("--channel", type=int, default=1, help="Camera channel number to capture")
    parser.add_argument("--interval-hours", type=float, default=1.0, help="How many hours between screenshots")
    parser.add_argument("--output-dir", default="./screenshots", help="Folder for generated JPG files")
    parser.add_argument("--username", help="Hikvision username")
    parser.add_argument("--password", help="Hikvision password")
    parser.add_argument("--test-screenshot", action="store_true", help="Generate a sample screenshot without contacting the NVR")
    args = parser.parse_args()

    if args.test_screenshot:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / "test_snapshot.png"
        file_path.write_bytes(build_test_jpeg())
        print(f"Created test screenshot: {file_path}")
        return 0

    if not args.date:
        parser.error("--date is required unless using --test-screenshot")

    username = args.username or prompt_for_value("Hikvision username")
    password = args.password or getpass.getpass("Hikvision password: ")

    if not username or not password:
        print("Username and password are required.", file=sys.stderr)
        return 1

    base_url = f"http://{args.host}"
    print(f"Logging into {base_url}...")

    try:
        session = login_to_hikvision(args.host, username, password)
    except Exception as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir) / args.date.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Capturing screenshots for {args.date.isoformat()} every {args.interval_hours} hour(s) on channel {args.channel}...")
    for timestamp in build_capture_times(args.date, args.interval_hours):
        saved_path = capture_snapshot(session, base_url, args.channel, timestamp, output_dir)
        if saved_path:
            print(f"Saved {saved_path}")
        else:
            print(f"No image returned for {timestamp.strftime('%Y-%m-%d %H:%M')}")

    print(f"Finished. Files are in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
