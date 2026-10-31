# House Screenshots

A small terminal app for extracting still screenshots from a Hikvision NVR at fixed intervals.

## Install

```bash
python3 -m pip install -r requirements.txt
```

## Environment setup (recommended)

Copy [.env.example](.env.example) to `.env` and fill in your real values:

```bash
cp .env.example .env
```

The scripts automatically read `.env` (for `daily_pipeline.py`) and support environment variables (for `app.py` and `play_rtsp.py`) so you can avoid committing credentials or host-specific settings.

## Test command (safe)

This generates a sample PNG without contacting your NVR:

```bash
python3 app.py --test-screenshot --output-dir ./demo
```

## Real usage

```bash
python3 app.py \
  --date 2026-10-27 \
  --channel 1 \
  --interval-hours 1 \
  --output-dir ./screenshots
```

If `RTSP_HOST`, `RTSP_USERNAME`, and `RTSP_PASSWORD` are in `.env`, you can omit `--host`, `--username`, and `--password`.

## Daily pipeline

Run one cycle now:

```bash
python3 daily_pipeline.py --once
```

Run as a daily daemon using values from `.env`:

```bash
python3 daily_pipeline.py
```

Override any value at runtime with CLI flags when needed.

You will be prompted for the Hikvision username and password.

The app will save one screenshot per interval into a folder like:

```txt
./screenshots/2026-10-27/
```

## Notes

- This targets Hikvision `ISAPI` snapshot endpoints.
- It is designed for quick hourly batch captures for one date.
- You can adjust the interval to 0.5, 1, 2, etc. when needed.
