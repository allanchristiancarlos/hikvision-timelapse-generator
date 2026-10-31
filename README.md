# House Screenshots

A modular terminal app for extracting still screenshots from a Hikvision NVR and rendering incremental timelapse videos.

## Project structure

- `house_screenshots/frame_capture_pipeline.py`: frame-capture pipeline module.
- `house_screenshots/timelapse_renderer.py`: incremental timelapse rendering module.
- `house_screenshots/pipeline_daemon.py`: background application service.
- `house_screenshots/capture_cli.py`: capture/render CLI composition root.
- `house_screenshots/daemon_cli.py`: scheduler CLI composition root.
- `play_rtsp.py`: user-facing extraction/render entrypoint.
- `daily_pipeline.py`: user-facing background scheduler entrypoint.

## Install

```bash
python3 -m pip install -r requirements.txt
```

## Docker helper scripts

Build image:

```bash
./scripts/docker-build.sh
```

Extract frames (passes through any play_rtsp.py args):

```bash
./scripts/docker-extract.sh --date 2026-10-15:2026-10-31 --frames-per-day 48
```

Render timelapse video:

```bash
./scripts/docker-render.sh
```

Run daemon once:

```bash
./scripts/docker-daemon-once.sh
```

Start daemon in background container:

```bash
./scripts/docker-daemon-start.sh
```

View daemon logs:

```bash
./scripts/docker-daemon-logs.sh
```

Stop and remove daemon container:

```bash
./scripts/docker-daemon-stop.sh
```

Optional environment variables:

- `HOUSE_SCREENSHOTS_IMAGE` to override image name (default: `house-screenshots`)
- `HOUSE_SCREENSHOTS_ENV_FILE` to use a different env file (default: `.env`)
- `HOUSE_SCREENSHOTS_CONTAINER_NAME` to rename daemon container (default: `house-screenshots-daemon`)

## Environment setup (recommended)

Copy [.env.example](.env.example) to `.env` and fill in your real values:

```bash
cp .env.example .env
```

The scripts automatically read `.env` and support environment variables so you can avoid committing credentials or host-specific settings.

## Capture and render pipeline

Extract frames:

```bash
python3 play_rtsp.py \
  --host "$RTSP_HOST" \
  --date 2026-10-15:2026-10-31 \
  --frames-per-day 48
```

Render/append timelapse from extracted frames:

```bash
python3 play_rtsp.py --render-video
```

## Background scheduler

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

The app saves one screenshot per interval into a folder like:

```txt
./screenshots/2026-10-27/
```

## Notes

- This targets Hikvision `ISAPI` snapshot endpoints.
- It is designed for quick hourly batch captures for one date.
- You can adjust the interval to 0.5, 1, 2, etc. when needed.
