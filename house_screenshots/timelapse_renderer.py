import calendar
import tempfile
from datetime import datetime
from pathlib import Path

from .frame_capture_pipeline import load_state, save_state
from .runtime import timed_subprocess_run


def collect_frame_images(frames_dir: Path):
    return sorted(frames_dir.glob("frame_*.jpg"))


def parse_frame_timestamp(frame_path: Path):
    name = frame_path.name
    if not (name.startswith("frame_") and name.endswith(".jpg")):
        return None
    body = name[len("frame_"):-len(".jpg")]
    try:
        return datetime.strptime(body, "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None


def write_image_concat_list(frame_paths, fps: int, list_path: Path):
    frame_duration = 1.0 / float(fps)
    lines = []
    for frame in frame_paths:
        escaped = str(frame.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
        lines.append(f"duration {frame_duration:.10f}")
    if frame_paths:
        escaped = str(frame_paths[-1].resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_video_segment_from_frames(frame_paths, fps: int, output_path: Path, env):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        list_path = Path(f.name)
    try:
        write_image_concat_list(frame_paths, fps, list_path)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-fps_mode",
            "cfr",
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        timed_subprocess_run(command, check=True, env=env, timeout=None, label="video segment render")
    finally:
        if list_path.exists():
            list_path.unlink()


def concat_videos(existing_video: Path, appended_video: Path, output_video: Path, env):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        list_path = Path(f.name)
    try:
        lines = []
        for path in [existing_video, appended_video]:
            escaped = str(path.resolve()).replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_video),
        ]
        timed_subprocess_run(command, check=True, env=env, timeout=None, label="video concat append")
    finally:
        if list_path.exists():
            list_path.unlink()


def render_video_target(frame_paths, output_video: Path, fps: int, state: dict, rerender_all: bool, env, label: str):
    state.setdefault("video_renders", {})
    render_key = str(output_video.resolve())
    render_state = state["video_renders"].get(
        render_key,
        {
            "fps": fps,
            "rendered_frames": [],
            "updated_at": None,
        },
    )

    rendered_names = set(render_state.get("rendered_frames", []))
    if rerender_all:
        print(f"Rerender-all enabled: rebuilding {output_video.name} from all {label} frames.")
        rendered_names = set()
        render_state["rendered_frames"] = []

    if output_video.exists() and not rendered_names and not rerender_all:
        video_mtime = output_video.stat().st_mtime
        inferred_rendered = [f.name for f in frame_paths if f.stat().st_mtime <= video_mtime]
        if inferred_rendered:
            render_state["rendered_frames"] = inferred_rendered
            rendered_names = set(inferred_rendered)
            print(f"Bootstrapped {output_video.name} state with {len(inferred_rendered)} frame(s).")

    new_frames = [f for f in frame_paths if f.name not in rendered_names]
    if not new_frames:
        if output_video.exists():
            print(f"No new {label} frames for {output_video.name}; skipping.")
        else:
            print(f"No {label} frames available to render {output_video.name}; skipping.")
        state["video_renders"][render_key] = render_state
        return

    print(f"Rendering {len(new_frames)} new {label} frame(s) at {fps} fps -> {output_video.name}")
    output_video.parent.mkdir(parents=True, exist_ok=True)

    temp_segment = output_video.with_suffix(".append_segment.mp4")
    temp_merged = output_video.with_suffix(".merged.mp4")
    temp_existing = output_video.with_suffix(".existing.mp4")

    try:
        build_video_segment_from_frames(new_frames, fps, temp_segment, env)

        if output_video.exists() and output_video.stat().st_size > 0 and not rerender_all:
            if temp_existing.exists():
                temp_existing.unlink()
            output_video.replace(temp_existing)
            concat_videos(temp_existing, temp_segment, temp_merged, env)
            temp_merged.replace(output_video)
        else:
            temp_segment.replace(output_video)

        rendered_list = render_state.get("rendered_frames", [])
        rendered_list.extend([f.name for f in new_frames])
        render_state["rendered_frames"] = rendered_list
        render_state["fps"] = fps
        render_state["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        render_state["output"] = str(output_video)
        state["video_renders"][render_key] = render_state

        print(f"Video ready: {output_video}")
    finally:
        for tmp in [temp_segment, temp_merged, temp_existing]:
            if tmp.exists():
                tmp.unlink()


def render_incremental_video(frames_dir: Path, output_video: Path, fps: int, state_path: Path, rerender_all: bool, env):
    if fps <= 0:
        raise ValueError("--video-fps must be greater than 0.")

    if not frames_dir.exists():
        raise ValueError(f"Frames folder does not exist: {frames_dir}")

    state = load_state(state_path)

    all_frames = collect_frame_images(frames_dir)
    if not all_frames:
        print("No frame images found to render.")
        return 0

    render_video_target(all_frames, output_video, fps, state, rerender_all, env, label="full")

    frames_by_month = {}
    for frame in all_frames:
        ts = parse_frame_timestamp(frame)
        if ts is None:
            continue
        month_num = ts.month
        frames_by_month.setdefault(month_num, []).append(frame)

    stem = output_video.stem
    suffix = output_video.suffix
    parent = output_video.parent

    for month_num in sorted(frames_by_month.keys()):
        month_short = calendar.month_abbr[month_num].lower()
        monthly_output = parent / f"{stem}-{month_short}{suffix}"
        render_video_target(
            frames_by_month[month_num],
            monthly_output,
            fps,
            state,
            rerender_all,
            env,
            label=month_short,
        )

    save_state(state_path, state)
    return 0
