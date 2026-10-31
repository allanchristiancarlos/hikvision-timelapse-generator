import os
import subprocess
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
