# Hikvision Timelapse Generator

A modular terminal app for extracting frames from a Hikvision NVR and rendering incremental timelapse videos.

## Project structure

- `src/hikvision_timelapse_generator/frame_capture_pipeline.py`: frame-capture pipeline module.
- `src/hikvision_timelapse_generator/timelapse_renderer.py`: incremental timelapse rendering module.
- `src/hikvision_timelapse_generator/pipeline_daemon.py`: background application service.
- `src/hikvision_timelapse_generator/capture_cli.py`: capture/render CLI composition root.
- `src/hikvision_timelapse_generator/daemon_cli.py`: scheduler CLI composition root.
- `src/capture_and_render.py`: user-facing extraction/render entrypoint.
- `src/run_capture_daemon.py`: user-facing background scheduler entrypoint.

## Install

```bash
python3 -m pip install -r requirements.txt
```

## Docker helper scripts

Build image:

```bash
./scripts/docker-build.sh
```

Extract frames (passes through any src/capture_and_render.py args):

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

- `HIKVISION_TIMELAPSE_GENERATOR_IMAGE` to override image name (default: `hikvision-timelapse-generator`)
- `HIKVISION_TIMELAPSE_GENERATOR_ENV_FILE` to use a different env file (default: `.env`)
- `HIKVISION_TIMELAPSE_GENERATOR_CONTAINER_NAME` to rename daemon container (default: `hikvision-timelapse-generator-daemon`)

## Environment setup (recommended)

Copy [.env.example](.env.example) to `.env` and fill in your real values:

```bash
cp .env.example .env
```

The scripts automatically read `.env` and support environment variables so you can avoid committing credentials or host-specific settings.

## Capture and render pipeline

Extract frames:

```bash
python3 src/capture_and_render.py \
  --host "$RTSP_HOST" \
  --date 2026-10-15:2026-10-31 \
  --frames-per-day 48
```

Render/append timelapse from extracted frames:

```bash
python3 src/capture_and_render.py --render-video
```

## Background scheduler

Run one cycle now:

```bash
python3 src/run_capture_daemon.py --once
```

Run as a daily daemon using values from `.env`:

```bash
python3 src/run_capture_daemon.py
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
