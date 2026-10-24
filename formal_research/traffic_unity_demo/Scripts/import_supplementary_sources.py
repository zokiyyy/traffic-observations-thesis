#!/usr/bin/env python3
"""Add five verified full original recordings and saved 10-second states to Unity.

All supplementary analysis outputs are read-only. No inference or video rendering is
performed. The existing five adapted sources are kept byte-for-byte unchanged.
"""

import copy
import hashlib
import json
import math
import struct
import subprocess
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
OUTPUTS = PROJECT.parent / "traffic_yolo_system" / "outputs"
STATES = OUTPUTS / "supplementary_states_2026-09-28_v1"
DELIVERY = OUTPUTS / "supplementary_five_2026-09-28_v3_repair"
SELECTED = DELIVERY / "selection" / "selected_sources.json"
OUT = PROJECT / "Assets" / "StreamingAssets" / "five_sources"
ORIGINAL_CATALOG = OUT / "original_catalog_v1.json"
CATALOG = OUT / "catalog.json"
VERIFY = PROJECT / "Verification" / "ten_sources"
FFPROBE = PROJECT.parent / "traffic_yolo_system" / "tools" / "full_video" / "ffprobe_7_1_1_arm64" / "ffprobe"
CASES = ["Newport", "NE12th", "Eastgate", "SE38th", "NE8th"]
LABELS = {"road_left": "Left road", "road_upper_left": "Upper-left road", "road_top": "Upper road",
          "road_right": "Right road", "road_bottom": "Lower road", "junction_center": "Junction center"}
COLORS = {"road_left": "#FFD700", "road_upper_left": "#FF8C42", "road_top": "#5ADC50",
          "road_right": "#4196FF", "road_bottom": "#37B6B6", "junction_center": "#D25AE6"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb", buffering=1024 * 1024) as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")


def write_detection_boxes(case, state, adapted, pts, video_hash):
    """Pack saved per-frame tracked detections for fast, source-PTS-driven Unity display."""
    camera = state["source"]["id"].split("__", 1)[0]
    attempt = DELIVERY / "delivery" / camera / "full_data_archive" / "attempt-000001"
    source = attempt / "frames.jsonl"
    receipt = read(attempt / "completion.json")
    require(receipt["source_id"] == state["source"]["id"], f"{case}: detection source ID mismatch")
    require(source.stat().st_size == receipt["files"]["frames.jsonl"]["bytes"],
            f"{case}: saved detection file size mismatch")
    width, height = state["source"]["width"], state["source"]["height"]
    zones = {z["id"]: i for i, z in enumerate(adapted["zones"])}
    classes = {"car": 0, "motorcycle": 1, "bus": 2, "truck": 3}
    time_base = state["source"]["time_base"]
    num, den = time_base["num"], time_base["den"]
    offsets = [0]
    records = bytearray()
    source_hash = hashlib.sha256()
    frames_with_boxes = tracks_total = 0
    with source.open("rb", buffering=1024 * 1024) as stream:
        for index, line in enumerate(stream):
            source_hash.update(line)
            row = json.loads(line)
            require(index < len(pts) and row["frame_index"] == index and row["source_id"] == state["source"]["id"],
                    f"{case}: detection frame identity mismatch at {index}")
            require(abs(row["pts"] * num / den - pts[index]) < 1e-7,
                    f"{case}: detection/source PTS mismatch at {index}")
            tracks = row["tracks_primary"] if row["valid"] else []
            require(isinstance(tracks, list) and len(tracks) < 65536,
                    f"{case}: invalid tracked-detection list at {index}")
            packed_tracks = bytearray()
            visible_count = 0
            for track in tracks:
                box = track["box_xyxy"]
                require(len(box) == 4 and all(math.isfinite(v) for v in box) and
                        box[2] > box[0] and box[3] > box[1],
                        f"{case}: invalid detection box at {index}")
                x1, y1, x2, y2 = [max(0., min(1., v / dimension)) for v, dimension in
                                  zip(box, (width, height, width, height))]
                if x2 <= x1 or y2 <= y1:
                    continue
                class_name = track["class_name"]
                require(class_name in classes and track["class_id"] in (0, 1, 2, 3),
                        f"{case}: unsupported vehicle class at {index}")
                track_id = int(track["track_id"])
                confidence = float(track["confidence"])
                require(0 <= track_id < 2**32 and math.isfinite(confidence) and 0 <= confidence <= 1,
                        f"{case}: invalid track identifier or confidence at {index}")
                zone = zones.get(track["assigned_zone"], 255)
                packed_tracks.extend(struct.pack("<HHHHIBBBB", *(round(v * 65535) for v in (x1, y1, x2, y2)),
                                                 track_id, classes[class_name], zone, round(confidence * 255), 0))
                visible_count += 1
            records.extend(struct.pack("<H", visible_count))
            records.extend(packed_tracks)
            if visible_count:
                frames_with_boxes += 1
            tracks_total += visible_count
            offsets.append(len(records))
    require(len(offsets) == len(pts) + 1, f"{case}: detection frame count mismatch")
    require(source_hash.hexdigest() == receipt["files"]["frames.jsonl"]["sha256"],
            f"{case}: saved detection SHA256 mismatch")
    require(len(records) < 2**32, f"{case}: detection cache exceeds format limit")
    relative = f"five_sources/Supplementary_{case}/detection_boxes.bin"
    target = PROJECT / "Assets" / "StreamingAssets" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".bin.tmp")
    with temporary.open("wb") as stream:
        stream.write(struct.pack("<8sIII32s", b"TRBOX1\0\0", width, height, len(pts), bytes.fromhex(video_hash)))
        stream.write(struct.pack(f"<{len(offsets)}I", *offsets))
        stream.write(records)
    temporary.replace(target)
    return {"path": relative, "sha256": digest(target), "bytes": target.stat().st_size,
            "source_frames_path": str(source), "source_frames_sha256": source_hash.hexdigest(),
            "frames_with_boxes": frames_with_boxes, "tracks_total": tracks_total}


def verified_pts(case, selected, state):
    path = Path(selected["source_path"])
    src = state["source"]
    require(path.is_file() and path.name == src["id"], f"{case}: original full recording missing or source ID mismatch")
    require(selected["source_id"] == src["id"] and selected["split"] == src["split"], f"{case}: selected/state identity mismatch")
    actual_hash = digest(path)
    require(actual_hash == src["sha256"], f"{case}: original full recording SHA256 mismatch")
    probe = json.loads(subprocess.run([str(FFPROBE), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,time_base,duration,nb_frames", "-of", "json", str(path)],
        check=True, capture_output=True, text=True).stdout)["streams"][0]
    require(probe["width"] == src["width"] == 1280 and probe["height"] == src["height"] == 720,
            f"{case}: original video dimensions mismatch")
    num, den = map(int, probe["time_base"].split("/"))
    require((num, den) == (src["time_base"]["num"], src["time_base"]["den"]), f"{case}: time base mismatch")
    # MP4 stream.duration can stop at the last packet PTS while the audited
    # source duration includes that packet's display interval.
    require(abs(float(probe["duration"]) - src["duration_s"]) < .1, f"{case}: video duration mismatch")
    require(int(probe["nb_frames"]) == src["frame_count"], f"{case}: frame count mismatch")
    camera = src["id"].split("__", 1)[0]
    timing = DELIVERY / "delivery" / camera / "full_data_archive" / "output" / "timing_frames.jsonl"
    ticks = []
    with timing.open(encoding="utf-8") as stream:
        for i, line in enumerate(stream):
            row = json.loads(line)
            require(row["frame_index"] == i and row["width"] == 1280 and row["height"] == 720,
                    f"{case}: audited timing row mismatch")
            ticks.append(int(row["pts"]))
    require(len(ticks) == src["frame_count"] and ticks[0] == 0 and
            all(a < b for a, b in zip(ticks, ticks[1:])), f"{case}: audited PTS ordering mismatch")
    packet_text = subprocess.run([str(FFPROBE), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True).stdout
    packet_pts = sorted(int(line.strip()) for line in packet_text.splitlines() if line.strip())
    require(packet_pts == ticks, f"{case}: full original MP4 packets differ from audited source PTS")
    require(0 <= ticks[-1] * num / den < src["duration_s"] and
            src["duration_s"] - ticks[-1] * num / den < 1,
            f"{case}: last PTS outside source duration")
    require(all(ticks[h["frame_index"]] == h["start_ticks"] and
                ticks[h["frame_index"] + 1] == h["end_ticks"] for h in state["held_intervals"]),
            f"{case}: held interval PTS mismatch")
    return [t * num / den for t in ticks], actual_hash, str(path), str(timing), probe


def adapt(case, state, selected, pts, video_hash, video_path):
    data = copy.deepcopy(state)
    data["schema_version"] = "ten_source_demo_v1"
    src = data["source"]
    src.update(video_path=video_path, video_sha256=video_hash, video_width=src["width"],
               video_height=src["height"], content_x=0, content_y=0,
               content_width=src["width"], content_height=src["height"],
               last_frame_time_s=pts[-1], frame_pts_s=pts, night_case=case == "NE8th",
               state_path=str(STATES / case / "states_10s.json"),
               rules_path=str(STATES / case / "rules_and_thresholds.json"),
               report_path=str(STATES / case / "中文结果说明.md"),
               state_sha256=digest(STATES / case / "states_10s.json"),
               rules_sha256=digest(STATES / case / "rules_and_thresholds.json"))
    if case == "NE8th":
        src["limitation"] = "Night Validation · all four composite states remain unknown; low detections do not prove free flow."
    elif case == "NE12th":
        src["limitation"] = "Short Train case · 408.577 s. Four regions lack motion calibration; unknown remains unknown."
    else:
        src["limitation"] = "Saved offline tracked boxes over the full original video; not live inference or validated congestion."
    data["notes"] = ["Full original recording with saved per-frame tracked boxes and ROI outlines; not live inference.",
                     "10-second summaries are offline and appear only after window end.",
                     "Main state uses conservative evidence_state. A count-only sustained hint is separate and cannot replace unknown.",
                     "Each recording calibrates its own P33/P67 count and P25 motion thresholds. Cross-recording grades are not comparable.",
                     "Image motion is pixels per second, not road speed. No congestion accuracy has been validated."]
    for zone in data["zones"]:
        threshold = state["thresholds"][zone["id"]]
        zone.update(label=LABELS[zone["id"]], color_hex=COLORS[zone["id"]],
                    polygon=[{"x": x / src["width"], "y": y / src["height"]} for x, y in zone["polygon_points"]],
                    density_low_upper=threshold["count_p33"], density_high_lower=threshold["count_p67"],
                    motion_low_upper=threshold["motion_low_upper"],
                    density_calibration_windows=threshold["count_calibration_windows"],
                    motion_calibration_windows=threshold["motion_calibration_windows"],
                    count_thresholds_available=threshold["count_p33"] is not None and threshold["count_p67"] is not None,
                    count_calibration_available=bool(threshold["count_thresholds_distinguishable"]),
                    motion_calibration_available=threshold["motion_low_upper"] is not None)
    for window in data["windows"]:
        for value in window["zones"]:
            value.update(state_label=value["state_hint_label"], reason=value["frozen_rule_reason"],
                         count_available=value["count_mean"] is not None,
                         motion_low_relative_available=value["motion_low_relative"] is not None,
                         density_level=value["relative_count_level"], density_label=value["relative_count_label"],
                         traffic_state=value["state_hint"], traffic_state_label=value["state_hint_label"])
    data["milestones"] = [{"time_s": 0, "label": "Start: collect the first 10-second window"}]
    for kind, label in [("joint_hint", "Joint count + image-motion hint"),
                        ("high_count_hint", "Sustained high-count hint"),
                        ("unknown", "Evidence insufficient; main state unknown")]:
        found = next((w for w in data["windows"] if w["complete"] and w["full_valid_window"] and
                      any(z["evidence_state"] == kind for z in w["zones"]) and w["end_s"] < src["duration_s"]), None)
        if found:
            data["milestones"].append({"time_s": found["end_s"], "label": label})
    return data


def main():
    require(read(STATES / "final_verification.json")["completed"], "Supplementary state verification incomplete")
    require(read(DELIVERY / "supplementary_results_index.json")["completed"], "Supplementary video delivery incomplete")
    selected = {e["source_id"]: e for e in read(SELECTED)["entries"]}
    if not ORIGINAL_CATALOG.exists():
        base = read(CATALOG)
        require(base["schema_version"] == "five_source_catalog_v1" and len(base["entries"]) == 5,
                "The original five-source catalogue is unavailable")
        ORIGINAL_CATALOG.write_bytes(CATALOG.read_bytes())
    base = read(ORIGINAL_CATALOG)
    require(len(base["entries"]) == 5, "Original source count changed")
    catalogue = {"schema_version": "ten_source_catalog_v1", "entries": list(base["entries"])}
    report = {"schema_version": "ten_source_adaptation_v1", "sources": [],
              "original_catalog_sha256": digest(ORIGINAL_CATALOG), "original_entries_unchanged": True,
              "full_original_video_count": 5, "short_preview_used_as_full_video": False}
    for case in CASES:
        state_path = STATES / case / "states_10s.json"
        state = read(state_path)
        source = selected[state["source"]["id"]]
        pts, video_hash, video_path, timing_path, probe = verified_pts(case, source, state)
        adapted = adapt(case, state, source, pts, video_hash, video_path)
        boxes = write_detection_boxes(case, state, adapted, pts, video_hash)
        adapted["source"].update(detection_boxes_path=boxes["path"], detection_boxes_sha256=boxes["sha256"])
        for original, out in zip(state["windows"], adapted["windows"]):
            for key, expected in original.items():
                if key != "zones":
                    require(out[key] == expected, f"{case}: saved window field changed: {key}")
            for before, after in zip(original["zones"], out["zones"]):
                for key, expected in before.items():
                    require(after[key] == expected, f"{case}: saved observation changed: {key}")
        data_path = f"five_sources/Supplementary_{case}/traffic_demo.json"
        write(OUT / f"Supplementary_{case}" / "traffic_demo.json", adapted)
        catalogue["entries"].append({"id": state["source"]["id"], "label": case,
            "data_path": data_path, "data_sha256": digest(OUT / f"Supplementary_{case}" / "traffic_demo.json"),
            "source_state_path": adapted["source"]["state_path"], "source_state_sha256": adapted["source"]["state_sha256"],
            "source_rules_path": adapted["source"]["rules_path"], "source_rules_sha256": adapted["source"]["rules_sha256"],
            "video_path": video_path, "video_sha256": video_hash})
        report["sources"].append({"case": case, "split": state["source"]["split"],
            "video_path": video_path, "video_sha256": video_hash, "state_path": str(state_path),
            "state_sha256": digest(state_path), "timing_path": timing_path, "timing_sha256": digest(timing_path),
            "source_duration_s": state["source"]["duration_s"], "last_frame_pts_s": pts[-1],
            "frame_count": len(pts), "zones": len(adapted["zones"]), "windows": len(adapted["windows"]),
            "ffprobe_stream": probe, "saved_detection_boxes": boxes})
        print(f"{case}: full original MP4, {len(pts):,} audited PTS, {len(adapted['windows'])} windows verified", flush=True)
    require(len(catalogue["entries"]) == 10, "Ten-source base catalogue incomplete")
    # Re-running this importer must not erase recordings registered later.
    preserved = []
    if CATALOG.exists():
        existing = read(CATALOG)
        if existing.get("schema_version") == "ten_source_catalog_v1":
            known = {entry["id"] for entry in catalogue["entries"]}
            preserved = [entry for entry in existing.get("entries", []) if entry["id"] not in known]
            catalogue["entries"].extend(preserved)
    report["preserved_additional_entries"] = len(preserved)
    write(CATALOG, catalogue)
    VERIFY.mkdir(parents=True, exist_ok=True)
    report["catalog_sha256"] = digest(CATALOG)
    write(VERIFY / "adaptation_verification.json", report)


if __name__ == "__main__":
    main()
