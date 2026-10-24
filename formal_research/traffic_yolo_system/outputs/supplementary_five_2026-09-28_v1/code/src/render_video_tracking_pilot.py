#!/usr/bin/env python3
"""Render an existing, frame-complete tracking pilot without running inference.

The source video remains at its native resolution. A separate information panel
is added above it. ROI occupancy counts refer to detections observed in the
current frame, rather than predicted missing tracks or cumulative traffic flow.
"""

from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

import cv2
import numpy as np

try:
    from .roi_geometry import assign_box, assignment_metadata, boundary_policy, validate_layout
except ImportError:  # Direct script execution, including the existing CLI.
    from roi_geometry import assign_box, assignment_metadata, boundary_policy, validate_layout


PALETTE = [(70, 205, 255), (220, 160, 60), (120, 215, 90), (215, 100, 220)]
PANEL_HEIGHT = 150
DEFAULT_REVIEW_FRAMES = [0, 300, 1050, 1595, 2400, 3300]


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def resolve_ffmpeg(explicit: str | None) -> str:
    if explicit:
        candidate = shutil.which(explicit) or explicit
        if not Path(candidate).is_file():
            raise FileNotFoundError(f"FFmpeg not found: {candidate}")
        return str(candidate)
    candidate = shutil.which("ffmpeg")
    if candidate:
        return candidate
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError("FFmpeg is required: put it on PATH, use --ffmpeg, or install imageio-ffmpeg.") from exc


def read_zones(path: Path) -> dict:
    zones = json.loads(path.read_text(encoding="utf-8"))
    polygons = validate_layout(zones)
    zones["_assignment_polygons"] = polygons
    zones["_boundary_policy"] = boundary_policy(zones)
    for index, zone in enumerate(zones["zones"]):
        polygon = polygons[index][1]
        zone["_polygon"] = np.rint(polygon).astype(np.int32)
        zone["_color"] = tuple(zone.get("color_bgr", PALETTE[index % len(PALETTE)]))
    return zones


def ascii_label(value: object) -> str:
    return str(value).encode("ascii", errors="replace").decode("ascii")


def text(image: np.ndarray, value: str, xy: tuple[int, int], color=(240, 240, 240), scale=0.53, thickness=1, backdrop=False):
    value = ascii_label(value)
    if backdrop:
        (width, height), baseline = cv2.getTextSize(value, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
        cv2.rectangle(image, (xy[0] - 1, xy[1] - height - 2), (xy[0] + width + 2, xy[1] + baseline), (24, 24, 24), -1)
    cv2.putText(image, value, xy, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def zone_overlay(frame: np.ndarray, zones: dict) -> np.ndarray:
    output = frame.copy()
    fill = frame.copy()
    for zone in zones["zones"]:
        cv2.fillPoly(fill, [zone["_polygon"]], zone["_color"])
    cv2.addWeighted(fill, 0.13, output, 0.87, 0, output)
    for zone in zones["zones"]:
        polygon = zone["_polygon"]
        cv2.polylines(output, [polygon], True, zone["_color"], 2, cv2.LINE_AA)
        center = np.asarray(zone.get("label_anchor", polygon.mean(axis=0)), dtype=int)
        label = ascii_label(zone.get("display_name") or zone["zone_name"])
        label_width = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 1)[0][0]
        x = max(4, min(int(center[0] - label_width / 2), output.shape[1] - label_width - 4))
        y = max(18, min(int(center[1]), output.shape[0] - 6))
        text(output, label, (x, y), zone["_color"], 0.52, backdrop=True)
    return output


def validate_record(record: dict, frame_index: int, zones: dict, width: int, height: int):
    if record.get("frame_index") != frame_index:
        raise ValueError(f"Expected record for frame {frame_index}, got {record.get('frame_index')!r}.")
    for key in ("timestamp_seconds", "source_timestamp_seconds"):
        if not isinstance(record.get(key), (int, float)) or not math.isfinite(record[key]) or record[key] < 0:
            raise ValueError(f"Invalid {key} in frame {frame_index}.")
    known_zones = {zone["zone_name"] for zone in zones["zones"]}
    counts = {name: 0 for name in known_zones}
    outside = 0
    identifiers = set()
    tracks = record.get("tracks_primary")
    if not isinstance(tracks, list):
        raise ValueError(f"Missing tracks_primary list in frame {frame_index}.")
    for track in tracks:
        identifier = track["track_id"]
        if identifier in identifiers:
            raise ValueError(f"Duplicate track ID {identifier} in frame {frame_index}.")
        identifiers.add(identifier)
        box = track["box_xyxy"]
        if len(box) != 4 or not all(math.isfinite(float(value)) for value in box):
            raise ValueError(f"Invalid box in frame {frame_index}.")
        x1, y1, x2, y2 = box
        if not (x1 < x2 and y1 < y2):
            raise ValueError(f"Reversed or zero-area box {box} in frame {frame_index}.")
        # Kalman-updated boxes may slightly exceed the image boundary. Retain
        # those coordinates for geometric assignment; clipping is display-only.
        if any(abs(float(value)) > limit for value, limit in zip(box, (width * 10, height * 10, width * 10, height * 10))):
            raise ValueError(f"Implausibly distant box {box} in frame {frame_index}.")
        if not 0 <= track["confidence"] <= 1:
            raise ValueError(f"Invalid confidence in frame {frame_index}.")
        assigned = track.get("assigned_zone")
        if assigned in known_zones:
            counts[assigned] += 1
        elif assigned in (None, "", "outside", "outside_zones", "outside_roi"):
            outside += 1
        else:
            raise ValueError(f"Unknown assigned zone {assigned!r} in frame {frame_index}.")
        geometric_zone = assign_box(box, zones["_assignment_polygons"], zones["_boundary_policy"])
        if (geometric_zone != "outside_roi" and assigned != geometric_zone) or (geometric_zone == "outside_roi" and assigned in known_zones):
            raise ValueError(f"Track {identifier} zone assignment disagrees with bottom-center geometry in frame {frame_index}.")
    if record.get("zone_counts") != counts:
        raise ValueError(f"zone_counts disagree with observed tracks in frame {frame_index}.")
    if record.get("total_in_zones") != sum(counts.values()) or record.get("outside_zone_count") != outside:
        raise ValueError(f"Occupancy totals disagree with tracks in frame {frame_index}.")


def render_frame(frame: np.ndarray, record: dict, zones: dict, tails: dict, fps: float, canvas_width: int) -> np.ndarray:
    overlay = zone_overlay(frame, zones)
    frame_index = record["frame_index"]
    known_zones = {zone["zone_name"]: zone for zone in zones["zones"]}
    present_ids = set()
    tail_length = max(2, int(round(fps)))
    for track in record["tracks_primary"]:
        identifier = track["track_id"]
        present_ids.add(identifier)
        raw_x1, raw_y1, raw_x2, raw_y2 = track["box_xyxy"]
        clamp_x = lambda value: max(0, min(frame.shape[1] - 1, int(round(value))))
        clamp_y = lambda value: max(0, min(frame.shape[0] - 1, int(round(value))))
        x1, y1, x2, y2 = clamp_x(raw_x1), clamp_y(raw_y1), clamp_x(raw_x2), clamp_y(raw_y2)
        point = (clamp_x((raw_x1 + raw_x2) / 2), clamp_y(raw_y2))
        in_zone = track.get("assigned_zone") in known_zones
        color = known_zones[track["assigned_zone"]]["_color"] if in_zone else (150, 150, 150)
        previous = tails.get(identifier)
        if previous is None or previous["last_frame"] != frame_index - 1:
            previous = {"last_frame": frame_index, "points": deque(maxlen=tail_length)}
        previous["last_frame"] = frame_index
        previous["points"].append(point)
        tails[identifier] = previous
        if in_zone and len(previous["points"]) >= 2:
            cv2.polylines(overlay, [np.asarray(previous["points"], dtype=np.int32)], False, color, 1, cv2.LINE_AA)
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2 if in_zone else 1)
        cv2.circle(overlay, point, 3 if in_zone else 2, color, -1, cv2.LINE_AA)
        # Keep small, distant vehicles readable without long overlapping labels.
        label = f"#{identifier}" if x2 - x1 < 55 or y2 - y1 < 35 else f"#{identifier} {track['class_name']}"
        label_width = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.39, 1)[0][0]
        text(overlay, label, (min(x1, max(0, frame.shape[1] - label_width - 2)), max(13, min(frame.shape[0] - 6, y1 - 4))), color, 0.39, backdrop=True)
    # Delete absent tracks immediately: no extrapolated positions or bridging gaps.
    for identifier in set(tails).difference(present_ids):
        del tails[identifier]
    canvas_height = frame.shape[0] + PANEL_HEIGHT
    canvas_height += canvas_height % 2
    canvas = np.full((canvas_height, canvas_width, 3), (26, 26, 26), dtype=np.uint8)
    canvas[PANEL_HEIGHT:PANEL_HEIGHT + frame.shape[0], :frame.shape[1]] = overlay
    title = "YOLO11s + ByteTrack | Validation pilot"
    if "background_confirmation" in record:
        title = "YOLO11s + ByteTrack | Background confirmation (pilot)"
    if "contained_vehicle_suppression" in record:
        title = "YOLO11s + ByteTrack | Heavy-vehicle dedup (pilot v5)"
    if zones.get("boundary_tie_break") == "zone_order":
        title += " | Shared-border ROIs v2"
    text(canvas, title, (16, 28), (250, 250, 250), 0.72, 2)
    text(canvas, f"Clip time {record['timestamp_seconds']:7.2f}s | Source time {record['source_timestamp_seconds']:8.2f}s | Frame {frame_index}", (16, 53), scale=0.52)
    x = 16
    for zone in zones["zones"]:
        label = f"{zone.get('display_name') or zone['zone_name']}: {record['zone_counts'][zone['zone_name']]}"
        text(canvas, label, (x, 78), zone["_color"], 0.51, 1)
        x += cv2.getTextSize(ascii_label(label), cv2.FONT_HERSHEY_SIMPLEX, 0.51, 1)[0][0] + 24
    text(canvas, f"Inside road zones: {record['total_in_zones']} | Outside zones (excluded): {record['outside_zone_count']}", (16, 102), scale=0.53)
    text(canvas, "Current visible vehicles; not cumulative flow", (16, 125), (205, 220, 255), 0.52)
    text(canvas, "PROVISIONAL road zones | Observed tracks only | Counts are not congestion labels", (16, 144), (175, 175, 175), 0.41)
    return canvas


def verify_video(path: Path, expected_frames: int, expected_width: int, expected_height: int, expected_fps: float) -> dict:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError("Rendered video could not be reopened.")
    actual_fps = capture.get(cv2.CAP_PROP_FPS)
    decoded = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame.shape[:2] != (expected_height, expected_width):
                raise RuntimeError(f"Rendered frame {decoded} has unexpected dimensions {frame.shape[:2]}.")
            decoded += 1
    finally:
        capture.release()
    if decoded != expected_frames or abs(actual_fps - expected_fps) > 0.01:
        raise RuntimeError(f"Rendered video verification failed: frames={decoded}/{expected_frames}, FPS={actual_fps}/{expected_fps}.")
    return {"all_frames_decoded": True, "decoded_frames": decoded, "width": expected_width, "height": expected_height, "fps": actual_fps, "audio": False, "audio_note": "FFmpeg input is raw video only and output uses -an."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--zones", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--review-frames", type=int, nargs="+", default=DEFAULT_REVIEW_FRAMES)
    args = parser.parse_args()
    for name in ("source", "records", "zones"):
        path = getattr(args, name).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        setattr(args, name, path)
    args.output = args.output.resolve()
    args.review_dir = args.review_dir.resolve()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite output: {args.output}")
    if args.output.suffix.lower() != ".mp4":
        raise ValueError("Output must end in .mp4.")
    metadata_path = args.review_dir / "render_metadata.json"
    if metadata_path.exists():
        raise FileExistsError(f"Refusing to overwrite render metadata: {metadata_path}")
    selected_frames = set(args.review_frames)
    for frame_index in selected_frames:
        if frame_index < 0:
            raise ValueError("Review frame indices must be nonnegative.")
        for kind in ("clean", "roi", "overlay"):
            if (args.review_dir / f"frame_{frame_index:06d}_{kind}.jpg").exists():
                raise FileExistsError("Refusing to overwrite an existing review image.")
    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    zones = read_zones(args.zones)
    source = cv2.VideoCapture(str(args.source))
    if not source.isOpened():
        raise RuntimeError("Could not open source video.")
    width, height = int(source.get(cv2.CAP_PROP_FRAME_WIDTH)), int(source.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = source.get(cv2.CAP_PROP_FPS)
    advertised_frames = int(source.get(cv2.CAP_PROP_FRAME_COUNT))
    if (width, height) != (zones["frame_width"], zones["frame_height"]):
        source.release()
        raise ValueError(f"Video dimensions {width}x{height} disagree with zones.")
    if not math.isfinite(fps) or fps <= 0:
        source.release()
        raise ValueError("Invalid source frame rate.")
    canvas_width = max(960, width)  # Keep the information panel legible in small QA fixtures.
    canvas_width += canvas_width % 2
    canvas_height = height + PANEL_HEIGHT
    canvas_height += canvas_height % 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.review_dir.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(f".{args.output.stem}.rendering-{uuid.uuid4().hex}.mp4")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-n", "-f", "rawvideo", "-pixel_format", "bgr24", "-video_size", f"{canvas_width}x{canvas_height}", "-framerate", f"{fps:.10f}", "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(partial)]
    metadata = {"status": "running", "source": str(args.source), "records": str(args.records), "zones": str(args.zones), "output": str(args.output), "source_width": width, "source_height": height, "source_fps": fps, "source_advertised_frames": advertised_frames, "panel_height": PANEL_HEIGHT, "inference_performed": False, "track_input": "tracks_primary; current observed detections only", "tail_policy": "About one second; reset after any absent frame", "zone_assignment": "bottom_center from raw, unclipped tracking boxes; assignments and counts are validated from records", "zone_assignment_policy": assignment_metadata(zones), "display_clipping": "Only displayed boxes, points, tails and text anchors are clipped to image bounds", "zone_status": {zone["zone_name"]: zone.get("status", "unspecified") for zone in zones["zones"]}, "records_sha256": digest(args.records), "zones_sha256": digest(args.zones), "renderer_sha256": digest(Path(__file__)), "ffmpeg_command": command, "review_images": []}
    metadata["geometry_implementation_sha256"] = digest(Path(__file__).with_name("roi_geometry.py"))
    started = time.monotonic()
    process = None
    try:
        with tempfile.TemporaryFile() as encoder_errors, args.records.open(encoding="utf-8") as records_handle:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encoder_errors)
            frame_index = 0
            tails = {}
            while True:
                ok, frame = source.read()
                line = records_handle.readline()
                if not ok:
                    if line or any(line.strip() for line in records_handle):
                        raise ValueError(f"Records extend beyond the source video ({frame_index} decoded frames).")
                    break
                if not line:
                    raise ValueError(f"Missing tracking record for source frame {frame_index}.")
                if frame.shape[:2] != (height, width):
                    raise ValueError(f"Source frame {frame_index} has inconsistent dimensions.")
                record = json.loads(line)
                validate_record(record, frame_index, zones, width, height)
                if abs(record["timestamp_seconds"] - frame_index / fps) > max(0.005, 0.1 / fps):
                    raise ValueError(f"Record timestamp does not match frame/FPS at frame {frame_index}.")
                rendered = render_frame(frame, record, zones, tails, fps, canvas_width)
                try:
                    process.stdin.write(rendered.tobytes())
                except BrokenPipeError as exc:
                    process.wait(timeout=30)
                    encoder_errors.seek(0)
                    detail = encoder_errors.read().decode("utf-8", errors="replace")
                    raise RuntimeError(f"FFmpeg stopped accepting frames: {detail[-5000:]}") from exc
                if frame_index in selected_frames:
                    for kind, image in (("clean", frame), ("roi", zone_overlay(frame, zones)), ("overlay", rendered)):
                        destination = args.review_dir / f"frame_{frame_index:06d}_{kind}.jpg"
                        if not cv2.imwrite(str(destination), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                            raise RuntimeError(f"Could not write review image {destination}.")
                        metadata["review_images"].append({"frame_index": frame_index, "kind": kind, "path": str(destination)})
                frame_index += 1
                if frame_index % 600 == 0:
                    print(f"Rendered {frame_index} frames", flush=True)
            process.stdin.close()
            exit_code = process.wait(timeout=180)
            encoder_errors.seek(0)
            stderr = encoder_errors.read().decode("utf-8", errors="replace")
            if exit_code != 0:
                raise RuntimeError(f"FFmpeg failed with exit code {exit_code}: {stderr[-5000:]}")
            if not frame_index or (advertised_frames > 0 and frame_index != advertised_frames):
                raise ValueError(f"Source frame count mismatch: decoded {frame_index}, advertised {advertised_frames}.")
        verification = verify_video(partial, frame_index, canvas_width, canvas_height, fps)
        if args.output.exists():
            raise FileExistsError("Output appeared while rendering; refusing to overwrite it.")
        partial.rename(args.output)
        metadata.update(status="complete", frames=frame_index, duration_seconds=frame_index / fps, elapsed_seconds=round(time.monotonic() - started, 3), output_bytes=args.output.stat().st_size, output_sha256=digest(args.output), verification=verification)
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "complete", "output": str(args.output), "frames": frame_index, "metadata": str(metadata_path)}), flush=True)
    except Exception as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=30)
        if partial.exists():
            partial.unlink()
        metadata.update(status="failed", error=f"{type(exc).__name__}: {exc}", elapsed_seconds=round(time.monotonic() - started, 3))
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        raise
    finally:
        source.release()


if __name__ == "__main__":
    main()
