"""Turn saved, current-frame track observations into prototype road-area counts.

Does not invoke a model or infer flow, unique physical vehicles, or congestion.
Every decoded frame has a record, including zero-observation frames.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

try:
    from .roi_geometry import assign_box, assignment_metadata, boundary_policy, validate_layout
except ImportError:  # Direct script execution, including the existing CLI.
    from roi_geometry import assign_box, assignment_metadata, boundary_policy, validate_layout


def load(path):
    return json.loads(Path(path).read_text())


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assign_zone(box, zones, boundary_tie_break="error"):
    """Backwards-compatible wrapper around the shared ROI implementation."""
    return assign_box(box, zones, boundary_tie_break)


def track_diagnostics(observations, fps):
    records = []
    for track_id, obs in sorted(observations.items()):
        frames = [v[0] for v in obs]
        gaps = [b - a - 1 for a, b in zip(frames, frames[1:]) if b > a + 1]
        class_changes = sum(a[1] != b[1] for a, b in zip(obs, obs[1:]))
        records.append({
            "track_id": track_id, "first_frame": frames[0], "last_frame": frames[-1],
            "observation_frames": len(frames), "observed_seconds": len(frames) / fps,
            "span_seconds": (frames[-1] - frames[0] + 1) / fps,
            "missing_frames_within_span": sum(gaps), "gap_events": len(gaps),
            "class_changes": class_changes,
            "majority_observed_class": Counter(v[1] for v in obs).most_common(1)[0][0],
        })
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--zones", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    manifest, layout = load(args.manifest), load(args.zones)
    assert manifest["source_split"]["Formal Split"] == "Validation"
    assert layout["source_split"] == "Validation"
    assert layout["clip_sha256"] == manifest["clip_sha256"]
    fps, expected = float(manifest["fps"]), int(manifest["frame_count"])
    width, height = manifest["width"], manifest["height"]
    assert (layout["frame_width"], layout["frame_height"]) == (width, height)
    assert layout["assignment_point"] == "bottom_center"
    assert fps > 0 and expected > 0
    polygons = validate_layout(layout)
    policy = boundary_policy(layout)
    zone_names = [n for n, _ in polygons]

    args.output.mkdir(parents=True)
    frames, timeline, observations = [], [], {"primary": defaultdict(list), "high_input": defaultdict(list)}
    low_recoveries = 0
    zone_obs = {branch: Counter() for branch in observations}
    with args.records.open() as source, (args.output / "frames_with_zones.jsonl").open("x") as out:
        for index, line in enumerate(source):
            rec = json.loads(line)
            assert rec["frame_index"] == index
            assert abs(rec["timestamp_seconds"] - index / fps) < 1e-6
            assert abs(rec["source_timestamp_seconds"] - manifest["clip_start_seconds"] - index / fps) < 1e-6
            counts = {}
            for branch in observations:
                tracks = rec["tracks_" + branch]
                ids = [int(t["track_id"]) for t in tracks]
                assert len(ids) == len(set(ids)), "Duplicate track ID in one frame"
                counts[branch] = Counter()
                for t in tracks:
                    assert t["class_name"] in ["car", "motorcycle", "bus", "truck"]
                    assert np.isfinite(t["box_xyxy"]).all()
                    t["assigned_zone"] = assign_zone(t["box_xyxy"], polygons, policy)
                    counts[branch][t["assigned_zone"]] += 1
                    zone_obs[branch][t["assigned_zone"]] += 1
                    observations[branch][int(t["track_id"])].append((index, t["class_name"]))
                    if branch == "primary" and t["confidence"] < 0.35 - 1e-7:
                        low_recoveries += 1
            current = {z: counts["primary"][z] for z in zone_names}
            reference = {z: counts["high_input"][z] for z in zone_names}
            total = sum(current.values())
            outside = counts["primary"]["outside_roi"]
            assert total + outside == len(rec["tracks_primary"])
            rec.update(zone_counts=current, total_in_zones=total, outside_zone_count=outside,
                       reference_zone_counts=reference, reference_total_in_zones=sum(reference.values()))
            out.write(json.dumps(rec, allow_nan=False) + "\n")
            frame = {"frame_index": index, "timestamp_seconds": index / fps,
                     "source_timestamp_seconds": manifest["clip_start_seconds"] + index / fps,
                     **current, "total_in_zones": total, "outside_roi": outside,
                     "all_current_tracks": len(rec["tracks_primary"]),
                     "high_input_total_in_zones": sum(reference.values()),
                     "raw_detection_count": len(rec["raw_detections"])}
            frames.append(frame)
            timeline.append({"frame_index": index, "time_seconds": index / fps,
                             "source_time_seconds": rec["source_timestamp_seconds"],
                             "counts": current, "total_visible_in_selected_zones": total})
    assert len(frames) == expected, f"{len(frames)} frames; expected {expected}"
    with (args.output / "frame_zone_counts.csv").open("x", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(frames[0]))
        writer.writeheader()
        writer.writerows(frames)

    windows = []
    window_frames = int(round(fps * 10))
    for start in range(0, expected, window_frames):
        subset = frames[start:start + window_frames]
        for zone in zone_names:
            values = [f[zone] for f in subset]
            windows.append({"window_start_seconds": start / fps,
                            "window_end_seconds_exclusive": (start + len(subset)) / fps,
                            "frames": len(subset), "zone_name": zone,
                            "mean_current_vehicles": sum(values) / len(values),
                            "min_current_vehicles": min(values), "max_current_vehicles": max(values)})
    with (args.output / "zone_counts_10s.csv").open("x", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(windows[0]))
        writer.writeheader()
        writer.writerows(windows)

    diagnostics = {branch: track_diagnostics(obs, fps) for branch, obs in observations.items()}
    write_json(args.output / "track_diagnostics.json", diagnostics)
    summary = {
        "status": "completed", "frame_count": len(frames), "fps": fps,
        "duration_seconds": len(frames) / fps, "source_split": "Validation",
        "source_video": manifest["source_split"]["Source Video Filename"],
        "inputs_sha256": {"records": digest(args.records), "zones": digest(args.zones),
                          "manifest": digest(args.manifest)},
        "geometry_implementation_sha256": digest(Path(__file__).with_name("roi_geometry.py")),
        "count_definition": "Number of current-frame observed active tracks whose box bottom-centre lies in a selected ROI. No lost-track extrapolation; not cumulative unique vehicles or flow.",
        "zone_assignment_policy": assignment_metadata(layout),
        "all_frame_records_retained": True,
        "frames_with_no_tracks": sum(f["all_current_tracks"] == 0 for f in frames),
        "frames_with_no_in_zone_tracks": sum(f["total_in_zones"] == 0 for f in frames),
        "observations_below_0_35_in_primary": low_recoveries,
        "zones": {z: {"mean_current_vehicles": sum(f[z] for f in frames) / len(frames),
                       "min_current_vehicles": min(f[z] for f in frames),
                       "max_current_vehicles": max(f[z] for f in frames)} for z in zone_names},
        "tracking_diagnostics": {branch: {
            "track_ids_created_not_unique_vehicle_count": len(rows),
            "observations": sum(r["observation_frames"] for r in rows),
            "tracks_with_reappearance_gap": sum(r["gap_events"] > 0 for r in rows),
            "tracks_with_class_change": sum(r["class_changes"] > 0 for r in rows),
            "short_tracks_under_0_5s": sum(r["observed_seconds"] < 0.5 for r in rows),
            "in_zone_observations": sum(zone_obs[branch][z] for z in zone_names),
        } for branch, rows in diagnostics.items()},
        "limitations": ["ROIs provisional; foreground-obscured approach excluded.",
                        "No trajectory ground truth: diagnostic ID counts are not ID-switch accuracy, MOTA or IDF1.",
                        "Low-confidence recovery may retain wrong matches; more observations alone do not prove accuracy.",
                        "No density, speed, queue or congestion rule is fitted or validated."],
    }
    write_json(args.output / "summary.json", summary)
    write_json(args.output / "timeline.json", {
        "schema_version": 1, "video_fps": fps, "duration_seconds": expected / fps,
        "source_split": "Validation", "time_origin": "start of this 120-second clip",
        "source_offset_seconds": manifest["clip_start_seconds"],
        "count_definition": summary["count_definition"],
        "zone_assignment_policy": summary["zone_assignment_policy"],
        "zone_layout": layout, "frames": timeline,
    })
    print(json.dumps({"status": "completed", "frame_count": len(frames),
                      "zones": summary["zones"], "tracking_diagnostics": summary["tracking_diagnostics"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
