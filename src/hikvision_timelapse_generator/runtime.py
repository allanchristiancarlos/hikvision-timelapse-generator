import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse


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


def has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def redacted_command(command):
    redacted = []
    index = 0
    while index < len(command):
        token = command[index]
        lower_token = token.lower()

        if lower_token in {"--password", "--pass", "--username", "--user", "-p", "-u", "--rtsp-password", "--rtsp-user"}:
            redacted.append(token)
            if index + 1 < len(command):
                redacted.append("***")
                index += 2
                continue
            index += 1
            continue

        if "://" in token and "@" in token:
            parsed = urlparse(token)
            if parsed.hostname:
                host = parsed.hostname
                if parsed.port:
                    host = f"{host}:{parsed.port}"
                safe_url = urlunparse(parsed._replace(netloc=f"***:***@{host}"))
                redacted.append(safe_url)
                index += 1
                continue

        redacted.append(token)
        index += 1
    return redacted


class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def __init__(self, *args, force_color=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.force_color = force_color

    def format(self, record):
        message = super().format(record)
        color_prefix = self.COLORS.get(record.levelname, "")
        if not color_prefix or not (self.force_color or sys.stdout.isatty()):
            return message
        return f"{color_prefix}{message}{self.RESET}"


class DynamicStreamHandler(logging.StreamHandler):
    def __init__(self, stream_name="stdout"):
        super().__init__()
        self.stream_name = stream_name

    def emit(self, record):
        stream = sys.stdout if self.stream_name == "stdout" else sys.stderr
        self.stream = stream
        super().emit(record)


def configure_logging(level=logging.INFO, *, force_color=True):
    handler = DynamicStreamHandler("stdout")
    handler.setFormatter(ColorFormatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force_color=force_color,
    ))

    for logger_name in ("hikvision_timelapse_generator", "house_screenshots"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        logger.propagate = False
        logger.handlers.clear()
        logger.addHandler(handler)

    return logging.getLogger("hikvision_timelapse_generator")


def get_logger():
    return logging.getLogger("hikvision_timelapse_generator")


def timed_subprocess_run(command, *, env, timeout, label, check=True):
    logger = get_logger()
    started = time.perf_counter()
    try:
        result = subprocess.run(command, check=check, env=env, timeout=timeout)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        elapsed = time.perf_counter() - started
        logger.error("[%s] failed after %.2fs", label, elapsed)
        raise

    return result


configure_logging()
