# House Screenshots

A small terminal app for extracting still screenshots from a Hikvision NVR at fixed intervals.

## Install

```bash
python3 -m pip install -r requirements.txt
```

## Test command (safe)

This generates a sample PNG without contacting your NVR:

```bash
python3 app.py --test-screenshot --output-dir ./demo
```

## Real usage

```bash
python3 app.py \
  --host 192.168.1.27 \
  --date 2026-10-27 \
  --channel 1 \
  --interval-hours 1 \
  --output-dir ./screenshots
```

You will be prompted for the Hikvision username and password.

The app will save one screenshot per interval into a folder like:

```txt
./screenshots/2026-10-27/
```

## Notes

- This targets Hikvision `ISAPI` snapshot endpoints.
- It is designed for quick hourly batch captures for one date.
- You can adjust the interval to 0.5, 1, 2, etc. when needed.
