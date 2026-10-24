"""Independent, streaming audit/export for four explicitly selected camera sources.

Reads completed results only. Never imports the production pipeline, loads a
model, opens Test media, or modifies an attempt. Geometry/filter checks are
independently implemented below. Flags describe output behavior, not errors or
measured vehicle-count accuracy. All time aggregation uses original durations.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics
import sys


from selection_binding import selected_entry, bind_prepared, MODEL_SHA
CONTAINMENT_SHA = "b299d573ecfcf8e609fc216f29b65ebcb288347def0e520cda404b29dcbf0ff6"
CONTAINMENT_FIELDS = {"schema_version":2,"heavy_classes":[2,3],"min_containment":.9,"min_area_ratio":1.2,
    "max_bottom_gap_fraction":.15,"min_keeper_confidence":.35,"ambiguity_min_containment":.5,
    "representative_policy":"confident_same_class_outer_then_low_confidence","development_only":True}
GEOM_EPS = 1e-9
TIME_EPS = 2e-8
CLASS_NAMES = {0: "car", 1: "motorcycle", 2: "bus", 3: "truck"}
TIMELINE_KEYS = (
    "record_type", "frame_index", "timestamp_seconds", "source_timestamp_seconds",
    "duration_seconds", "valid", "validity", "missing_reason", "zone_counts",
    "total_in_zones", "outside_zone_count", "all_current_tracks", "raw_detection_count",
)
FLAGS = {
    "adjacent_valid_zone_count_absolute_jump_at_least": 3,
    "continuous_all_road_zero_seconds_at_least": 1.0,
    "raw_detector_saturation_count_at_least": 300,
    "remaining_heavy_overlap_intersection_over_smaller_area_at_least": .5,
    "original_frame_presentation_duration_seconds_above": .5,
    "frozen_before_full_run_output_review": True,
    "interpretation": "Candidates for diagnostics, not labelled detector/count errors.",
}


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def canonical_timing(row, first_pts, tick):
    """Normalize explicit PTS/duration fields across ffprobe versions.

    Never substitute DTS or best-effort timestamps. Both spellings, when
    present, must agree. Integer clock values remain the time authority.
    """
    def field(primary, alternate):
        if primary in row and alternate in row and row[primary] != row[alternate]:
            raise ValueError(f"Contradictory {primary}/{alternate} in timing evidence")
        value=row.get(primary,row.get(alternate))
        if type(value) is not int:
            raise ValueError(f"Timing evidence requires original integer {primary}")
        return value
    pts=field("pts","pkt_pts");duration=field("duration","pkt_duration")
    if duration<=0:raise ValueError("Original frame duration must be positive")
    for key in ("frame_index","width","height"):
        if type(row.get(key)) is not int:raise ValueError(f"Timing {key} must be integer")
    normalized={"frame_index":row["frame_index"],"pts":pts,"duration_ticks":duration,
                "width":row["width"],"height":row["height"],
                "source_seconds":float((pts-first_pts)*tick),"duration_seconds":float(duration*tick)}
    for key,value in (("source_seconds",normalized["source_seconds"]),
                      ("original_pts_seconds",float(pts*tick)),
                      ("duration_seconds",normalized["duration_seconds"])):
        if key in row and (type(row[key]) not in (int,float) or not math.isfinite(row[key]) or abs(row[key]-value)>TIME_EPS):
            raise ValueError(f"Timing {key} contradicts original integer clock")
    return normalized


def write_json(path, data):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


class Audit:
    def __init__(self):
        self.check_count = 0
        self.errors = Counter()
        self.examples = []

    def check(self, condition, label, context=None):
        self.check_count += 1
        if not condition:
            self.errors[label] += 1
            if len(self.examples) < 50:
                self.examples.append({"check": label, "context": context})
        return bool(condition)

    def equal(self, value, expected, label, context=None):
        return self.check(value == expected, label, context)

    def close(self, value, expected, label, context=None, tol=TIME_EPS):
        return self.check(isinstance(value, (float, int)) and not isinstance(value, bool)
                          and math.isfinite(value) and abs(value - expected) <= tol,
                          label, context)


def point_position(point, polygon):
    """Independent scalar ray casting: -1 outside, 0 edge, 1 interior."""
    x, y = point
    inside = False
    for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1]):
        cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax)
        if (abs(cross) <= GEOM_EPS and min(ax, bx) - GEOM_EPS <= x <= max(ax, bx) + GEOM_EPS
                and min(ay, by) - GEOM_EPS <= y <= max(ay, by) + GEOM_EPS):
            return 0
        if (ay > y) != (by > y):
            if x < ax + (y - ay) * (bx - ax) / (by - ay):
                inside = not inside
    return 1 if inside else -1


def assign(box, zones):
    point = ((box[0] + box[2]) / 2, box[3])
    hits = [(z["zone_name"], point_position(point, z["polygon_points"])) for z in zones]
    hits = [(name, pos) for name, pos in hits if pos >= 0]
    if len(hits) > 1 and any(pos == 1 for _, pos in hits):
        raise ValueError(f"ROI interior overlap at {point}")
    return hits[0][0] if hits else "outside_roi"


def area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def intersection(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def iou(a, b):
    overlap = intersection(a, b)
    return overlap / (area(a) + area(b) - overlap)


def geometry_match(a, b):
    aa, ab = area(a), area(b)
    return (intersection(a, b) / min(aa, ab) >= .9 and max(aa, ab) / min(aa, ab) >= 1.2
            and abs(a[3] - b[3]) / min(a[3] - a[1], b[3] - b[1]) <= .15)


def expected_filters(raw, second, polygon=None):
    """Independently recalculate frozen indices; no production imports."""
    bg_keep, bg_candidates, bg_removed = [], [], []
    support = [x for x in second if x["confidence"] >= .35]
    for i, d in enumerate(raw):
        box = d["box_xyxy"]
        inside = polygon is not None and point_position(((box[0]+box[2])/2, (box[1]+box[3])/2), polygon) == 1
        keep = not inside or any(iou(box, s["box_xyxy"]) >= .5 for s in support)
        if inside:
            bg_candidates.append((i, keep))
        (bg_keep if keep else bg_removed).append(i)
    cross_keep, cross_removed = [], []
    for i in sorted(bg_keep, key=lambda i: (-raw[i]["confidence"], i)):
        keeper = next((k for k in cross_keep if raw[k]["class_id"] != raw[i]["class_id"]
                       and iou(raw[k]["box_xyxy"], raw[i]["box_xyxy"]) > .7), None)
        if keeper is None:
            cross_keep.append(i)
        else:
            cross_removed.append((i, keeper))
    cross_keep.sort()
    high = [i for i in cross_keep if raw[i]["class_id"] in (2, 3) and raw[i]["confidence"] >= .35]
    low = [i for i in cross_keep if raw[i]["class_id"] in (2, 3) and raw[i]["confidence"] < .35]
    high_keep, contain_removed = [], []
    for i in sorted(high, key=lambda i: (-area(raw[i]["box_xyxy"]), -raw[i]["confidence"], i)):
        keeper = next((k for k in high_keep if raw[k]["class_id"] == raw[i]["class_id"]
                       and geometry_match(raw[k]["box_xyxy"], raw[i]["box_xyxy"])), None)
        if keeper is None:
            high_keep.append(i)
        else:
            contain_removed.append((i, keeper, "A"))
    final = [i for i in cross_keep if raw[i]["class_id"] not in (2, 3)] + high_keep
    for i in sorted(low, key=lambda i: (-raw[i]["confidence"], i)):
        keeper = next((k for k in sorted(high_keep, key=lambda i: (-raw[i]["confidence"], i))
                       if geometry_match(raw[k]["box_xyxy"], raw[i]["box_xyxy"])), None)
        if keeper is None:
            final.append(i)
        else:
            contain_removed.append((i, keeper, "B"))
    final.sort()
    heavy = [i for i in final if raw[i]["class_id"] in (2, 3)]
    unresolved = [(i, j) for i, j in itertools.combinations(heavy, 2)
                  if intersection(raw[i]["box_xyxy"], raw[j]["box_xyxy"]) /
                  min(area(raw[i]["box_xyxy"]), area(raw[j]["box_xyxy"])) >= .5]
    return bg_keep, bg_candidates, bg_removed, cross_keep, cross_removed, final, contain_removed, unresolved


class Stats:
    """Independent accumulators; missing time remains excluded from means."""
    def __init__(self, names):
        self.valid = 0.0
        self.missing = 0.0
        self.integral = dict.fromkeys(names, 0.0)
        self.zero = dict.fromkeys(names, 0.0)
        self.minimum = dict.fromkeys(names)
        self.maximum = dict.fromkeys(names)
        self.valid_intersections = 0
        self.missing_intersections = 0

    def add(self, duration, counts):
        if counts is None:
            self.missing += duration
            self.missing_intersections += 1
            return
        self.valid += duration
        self.valid_intersections += 1
        for name, count in counts.items():
            self.integral[name] += duration * count
            self.zero[name] += duration if count == 0 else 0
            self.minimum[name] = count if self.minimum[name] is None else min(self.minimum[name], count)
            self.maximum[name] = count if self.maximum[name] is None else max(self.maximum[name], count)

    def zone(self, name):
        return {"mean_current_vehicles": self.integral[name] / self.valid if self.valid else None,
                "min_current_vehicles": self.minimum[name], "max_current_vehicles": self.maximum[name],
                "valid_zero_duration_seconds": self.zero[name],
                "observed_zero": self.maximum[name] == 0 if self.valid else None}


def add_windows(windows, begin, end, counts, start, stop, seconds=10):
    cursor = begin
    while cursor < end - 1e-12:
        k = min(len(windows)-1, int((cursor-start+1e-10)//seconds))
        piece_end = min(end, stop, start+(k+1)*seconds)
        if piece_end <= cursor:
            raise ValueError("Non-progressing independent window aggregation")
        windows[k].add(piece_end-cursor, counts)
        cursor = piece_end


def timeline_rows(attempt, audit):
    for entry in rows(attempt / "timeline/index.jsonl"):
        filename = entry["path"]
        if Path(filename).name != filename or not filename.startswith("part-"):
            raise ValueError("Unexpected timeline filename")
        n, first, last = 0, None, None
        for row in rows(attempt / "timeline" / filename):
            first = row["source_timestamp_seconds"] if first is None else first
            last = row["source_timestamp_seconds"] + row["duration_seconds"]
            n += 1
            yield row
        audit.equal(n, entry["records"], "timeline_index_record_count", filename)
        audit.close(first, entry["source_start_seconds"], "timeline_index_start", filename)
        audit.close(last, entry["source_end_seconds_exclusive"], "timeline_index_end", filename)


def check_detections(raw, audit, context, width, height):
    for detection in raw:
        box = detection.get("box_xyxy", [])
        valid_box = (len(box) == 4 and all(isinstance(v, (float, int)) and math.isfinite(v) for v in box)
                     and 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)
        if not audit.check(valid_box, "raw_box_finite_and_in_source", context):
            raise ValueError("Malformed raw box makes independent geometry unsafe")
        audit.equal(CLASS_NAMES.get(detection.get("class_id")), detection.get("class_name"), "raw_class_map", context)
        audit.check(0 <= detection.get("confidence", -1) <= 1, "raw_confidence_range", context)


def selected_timing(path, preflight, entry, phase):
    tick=Fraction(preflight["time_base_num"],preflight["time_base_den"])
    first=preflight["input_first_pts_ticks"]
    begin,end=preflight["first_frame_index"],preflight["end_frame_index_exclusive"]
    count=0; previous_end=None; first_bound=last_bound=None; bound_indices={}
    requested=entry.get("short_check_seconds",[]) if phase=="short" else []
    for raw in rows(path):
        row=canonical_timing(raw,first,tick)
        if row["frame_index"]!=count or (count==0 and row["pts"]!=first):
            raise ValueError("Original sidecar sequence or initial PTS mismatch")
        if previous_end is not None and row["pts"]!=previous_end:
            raise ValueError("Original sidecar has presentation overlap/gap")
        if [row["width"],row["height"]]!=[preflight["width"],preflight["height"]]:
            raise ValueError("Original sidecar dimensions changed")
        if requested:
            if requested[0] not in bound_indices and row["source_seconds"]>=requested[0]:
                bound_indices[requested[0]]=count
            if row["source_seconds"]<=requested[1]:
                bound_indices[requested[1]]=count
        if count==begin:first_bound=row["source_seconds"]
        if count==end:last_bound=row["source_seconds"]
        previous_end=row["pts"]+row["duration_ticks"]
        count+=1
        if begin<=row["frame_index"]<end:yield row
    if not count or count!=preflight["input_frame_count"]:
        raise ValueError("Original sidecar full EOF/count mismatch")
    if (row["pts"],row["duration_ticks"],previous_end-first)!=(preflight["input_last_pts_ticks"],preflight["final_frame_duration_ticks"],preflight["input_duration_ticks"]):
        raise ValueError("Original sidecar final clock mismatch")
    full_end=float((previous_end-first)*tick)
    if end==count:last_bound=full_end
    if first_bound is None or last_bound is None or abs(first_bound-preflight["source_start_seconds"])>TIME_EPS or abs(last_bound-preflight["source_end_seconds"])>TIME_EPS:
        raise ValueError("Selected interval is not its stated exact original PTS boundaries")
    if abs(full_end-preflight["input_duration_seconds"])>TIME_EPS:
        raise ValueError("Original full presentation duration mismatch")
    if phase=="short" and (len(requested)!=2 or [bound_indices.get(x) for x in requested]!=[begin,end]):
        raise ValueError("Short check differs from predeclared selection interval (ceil start and floor end original PTS)")


def audit_run(args):
    attempt = args.attempt.resolve()
    out = args.output.resolve()
    if out == attempt or attempt in out.parents:
        raise ValueError("Exports must not modify the completed attempt")
    out.mkdir(parents=True, exist_ok=True)
    if any((out/name).exists() for name in ("qc_summary.json", "frame_counts.csv", "window_counts_10s.csv", "flags.jsonl")):
        raise FileExistsError("Refusing to overwrite an existing audit/export")
    check = Audit()
    entry = selected_entry(args.selection, args.source_id)
    selection_sha = sha(args.selection)
    prepared, summary = load(attempt/"prepared.json"), load(attempt/"summary.json")
    task, config, camera, preflight = bind_prepared(entry, prepared)
    check.equal(load(attempt/"provenance.json"),prepared,"prepared_matches_provenance")
    fixed_detector={"conf":.1,"imgsz":640,"iou":.7,"half":False,"agnostic_nms":False,
                    "max_det":300,"augment":False,"rect":True,"batch":1,"classes":None}
    fixed_tracker={"tracker_type":"bytetrack","track_high_thresh":.35,"track_low_thresh":.1,
                   "new_track_thresh":.35,"track_buffer":30,"match_thresh":.8,"fuse_score":True}
    for key,value in fixed_detector.items():check.equal(config["detector"].get(key),value,"frozen_detector_setting",key)
    check.equal(config["tracker"],fixed_tracker,"frozen_tracker_settings")
    check.equal(config["dedup"]["mode"],"cross_class_greedy_iou","frozen_cross_class_mode")
    check.equal(config["dedup"]["iou_threshold"],.7,"frozen_cross_class_iou")
    check.equal(prepared["metadata"].get("synthetic", False), False, "not_synthetic")
    check.equal(config["input"]["kind"], "video", "actual_video_input")
    start, stop = preflight["source_start_seconds"], preflight["source_end_seconds"]
    first_index, end_index = preflight["first_frame_index"], preflight["end_frame_index_exclusive"]
    expected_count = end_index-first_index
    check.check(0 <= first_index < end_index <= preflight["input_frame_count"],"source_index_interval")
    check.equal(preflight["frame_count"], expected_count, "preflight_selected_frame_count")
    check.equal(preflight["time_mode"],"audited_pts","native_pts_mode")
    check.equal(config["aggregation"]["window_seconds"], 10, "ten_second_windows")
    if args.phase == "full":
        check.equal([first_index,end_index],[0,preflight["input_frame_count"]],"complete_original_source")
        check.equal(config.get("segment"),None,"full_source_no_segment")
    else:
        check.check(expected_count < preflight["input_frame_count"],"short_check_is_proper_segment")
        check.check(config.get("segment") is not None,"short_check_explicit_segment")
    if not (math.isfinite(start) and math.isfinite(stop) and stop>start):
        raise ValueError("A positive finite selected presentation interval is required")
    layout = load(args.layout) if args.layout else camera["layout"]
    check.equal(camera["layout"]["zones"], layout["zones"], "embedded_layout_matches")
    zones = layout["zones"]
    names = [z["zone_name"] for z in zones]
    if not names or len(names)!=len(set(names)):
        raise ValueError("Camera must provide nonempty uniquely named road regions")
    check.equal([layout["frame_width"],layout["frame_height"]],[preflight["width"],preflight["height"]],"layout_original_dimensions")
    # New cameras have no validated background exclusion. A Newport rule cannot
    # silently follow the generic duplicate/containment settings into this scope.
    check.equal(camera["background_filter"].get("mode"),"none","no_unreviewed_camera_background_rule")
    check.check(not any(k in camera["background_filter"] for k in ("policy","policy_path","policy_sha256")),"no_foreign_background_policy")
    background = None; bg_sha = None
    contained = config["dedup"]["containment_policy"]
    ct_sha = config["dedup"]["containment_policy_sha256"]
    check.equal(ct_sha,CONTAINMENT_SHA,"frozen_containment_policy_identity")
    for key,value in CONTAINMENT_FIELDS.items():
        check.equal(contained.get(key),value,"frozen_containment_field",key)
    time_hash = sha(args.pts)
    check.equal(time_hash,preflight["frame_timing_sha256"],"actual_runtime_pts_stream_identity")
    tick=Fraction(preflight["time_base_num"],preflight["time_base_den"])
    first_pts=preflight["input_first_pts_ticks"]
    status, receipt = load(attempt/"status.json"), load(attempt/"completion.json")
    check.equal(status["status"], "completed", "attempt_completed")
    check.equal(receipt["source_id"], entry["source_id"], "receipt_source")
    check.equal(receipt["fingerprint"], prepared["fingerprint"], "receipt_configuration")
    receipt_verified = 0
    for relative, details in receipt["files"].items():
        path = (attempt/relative).resolve()
        if attempt not in path.parents:
            raise ValueError("Receipt path escapes completed attempt")
        if check.check(path.is_file(), "receipt_file_exists", relative):
            check.equal(path.stat().st_size, details["bytes"], "receipt_file_size", relative)
            check.equal(sha(path), details["sha256"], "receipt_file_hash", relative)
            receipt_verified += 1
    actual_payload = {p.relative_to(attempt).as_posix() for p in attempt.rglob("*") if p.is_file()
                      and p.relative_to(attempt).as_posix() not in {"status.json", "run.log.jsonl", "completion.json", "failure.json"}}
    check.equal(actual_payload, set(receipt["files"]), "receipt_file_set")
    windows = [Stats(names) for _ in range(math.ceil((stop-start)/10))]
    global_stats = Stats(names)
    totals, flag_counts, class_totals, track_info = Counter(), Counter(), Counter(), {}
    time_rows = iter(selected_timing(args.pts,preflight,entry,args.phase)); timeline = iter(timeline_rows(attempt, check))
    previous_counts, previous_end, zero_start, zero_frames = None, start, None, 0
    peak_total, peak_total_time = -1, None
    frame_columns = ["frame_index", "source_timestamp_seconds", "duration_seconds", "valid", "missing_reason"] + names + ["total_in_zones", "outside_zone_count", "all_current_tracks", "raw_detection_count", "second_resolution_raw_count", "background_candidates", "background_removed", "cross_class_removed", "containment_removed", "unresolved_heavy_pairs"]
    with (out/"frame_counts.csv").open("x", newline="", encoding="utf-8-sig") as csv_file, (out/"flags.jsonl").open("x", encoding="utf-8") as flag_file:
        writer = csv.DictWriter(csv_file, fieldnames=frame_columns); writer.writeheader()
        def flag(kind, **data):
            flag_counts[kind] += 1
            flag_file.write(json.dumps({"kind": kind, **data}, ensure_ascii=False, allow_nan=False)+"\n")
        def finish_zero(end):
            nonlocal zero_start, zero_frames
            if zero_start is not None and end-zero_start >= 1-TIME_EPS:
                flag("all_road_zero_at_least_one_second", start_seconds=zero_start, end_seconds_exclusive=end,
                     duration_seconds=end-zero_start, frames=zero_frames)
            zero_start, zero_frames = None, 0
        for row in rows(attempt/"frames.jsonl"):
            idx = row.get("frame_index"); context = idx
            stamp, duration = row["source_timestamp_seconds"], row["duration_seconds"]
            end = stamp+duration
            check.close(stamp, previous_end, "continuous_output_time", context)
            check.check(duration > 0 and math.isfinite(duration), "positive_finite_duration", context)
            match = next(timeline, None)
            if check.check(match is not None, "timeline_row_exists", context):
                for key in TIMELINE_KEYS:
                    check.equal(match[key], row[key], "timeline_payload_equal", {"frame": idx, "field": key})
            check.equal(row["source"], prepared["metadata"]["source"], "frame_source_metadata", context)
            check.equal(row.get("configuration_sha256"),prepared["fingerprint"],"frame_configuration_identity",context)
            check.equal(row["split"], "Validation", "frame_validation_split", context)
            check.equal(row["traffic_state"], "not_computed", "no_unvalidated_traffic_state", context)
            if row["record_type"] != "frame":
                totals["generated_missing_intervals"] += 1
                check.equal(row["valid"], False, "missing_interval_invalid", context)
                check.equal(row["zone_counts"], None, "missing_interval_not_zero", context)
                global_stats.add(duration, None); add_windows(windows,stamp,end,None,start,stop)
                finish_zero(stamp); previous_counts=None; previous_end=end
                writer.writerow({"frame_index":None,"source_timestamp_seconds":stamp,"duration_seconds":duration,"valid":False,"missing_reason":row.get("missing_reason")})
                continue
            truth = next(time_rows, None)
            if truth is None:
                raise ValueError("Output contains more original frames than PTS evidence")
            check.equal(idx, first_index+totals["frames"], "sequential_original_frame_index", context)
            check.equal(row.get("segment_frame_index"),totals["frames"],"relative_segment_frame_index",context)
            check.close(row["timestamp_seconds"],stamp-start,"relative_selected_time",context)
            for key in ("pts","duration_ticks"):
                check.equal(row.get(key),truth[key],"exact_integer_"+key,context)
            check.equal([row.get("time_base_num"),row.get("time_base_den")],[tick.numerator,tick.denominator],"exact_frame_time_base",context)
            check.equal(idx, truth["frame_index"], "pts_frame_index", context)
            check.close(stamp, truth["source_seconds"], "original_frame_pts", context)
            check.close(duration, truth["duration_seconds"], "original_frame_duration", context)
            totals["frames"] += 1
            if duration>.5:
                flag("source_long_presentation_interval",frame_index=idx,source_seconds=stamp,duration_seconds=duration,
                     interpretation="Same original image held until next original PTS; no new visual observation inside this interval")
                totals["long_presentation_intervals"]+=1
                totals["long_presentation_duration_seconds"]+=duration
            valid = row["valid"]
            counts = dict.fromkeys(names, 0) if valid else None
            outside = 0; ids = set(); observed_indices=set()
            raw, second = row.get("raw_detections", []), row.get("confirmation_raw_detections", [])
            check_detections(raw,check,context,preflight["width"],preflight["height"]); check_detections(second,check,context,preflight["width"],preflight["height"])
            if valid:
                totals["valid_frames"] += 1
                quality=row.get("quality",{})
                for key,value in {"decoded":True,"inference_complete":True,"time_mode":"audited_pts","duration_semantics":"decoded_frame_presentation_interval","detector_max_det_reached":len(raw)==300}.items():
                    check.equal(quality.get(key),value,"frame_quality_"+key,context)
                check.check(len(raw)<=300,"detector_max_det_limit",context)
                check.close((row["decoder_position_msec"]-row["decoder_first_position_msec"])/1000,stamp,"decoder_original_pts_correspondence",context,tol=float(tick)+1e-7)
                check.check("raw_detections" in row,"raw_detector_array_present",context)
                check.check("confirmation_raw_detections" not in row and "background_confirmation" not in row,"no_camera_background_stage",context)
                check.check(isinstance(row.get("decoded_frame_sha256"),str) and len(row["decoded_frame_sha256"])==64,"decoded_frame_digest_present",context)
                bg, candidates, bg_remove, cross, cross_remove, final, removals, unresolved = expected_filters(raw,second,None)
                ca, ha = row["duplicate_suppression"], row["contained_vehicle_suppression"]
                check.equal(ha["policy_sha256"], ct_sha, "per_frame_containment_policy",context)
                check.equal(ca["input_detection_indices"], bg, "cross_stage_input",context)
                check.equal(ca["kept_detection_indices"], cross, "independent_cross_class_kept",context)
                check.equal([(x["removed_index"],x["kept_index"]) for x in ca["removed"]],cross_remove,"independent_cross_class_pairs",context)
                check.equal(ha["input_detection_indices"],cross,"containment_stage_input",context)
                check.equal(ha["kept_detection_indices"],final,"independent_containment_kept",context)
                check.equal([(x["removed_index"],x["kept_index"],x["stage"]) for x in ha["removed"]],removals,"independent_containment_pairs",context)
                check.equal([tuple(x["indices"]) for x in ha["unresolved_pairs"]],unresolved,"independent_unresolved_pairs",context)
                check.equal(row["tracker_input_detection_indices"], final, "tracker_input_indices",context)
                check.equal(len(raw),len(final)+len(bg_remove)+len(cross_remove)+len(removals),"filter_count_conservation",context)
                for track in row["tracks_primary"]:
                    track_id=track["track_id"]; raw_index=track["detection_index"]
                    check.check(track_id not in ids,"unique_track_id_within_frame",context);ids.add(track_id)
                    check.check(raw_index in final,"track_observed_from_final_detection",context)
                    check.check(raw_index not in observed_indices,"one_observed_track_per_detection",context);observed_indices.add(raw_index)
                    if not 0 <= raw_index < len(raw):
                        raise ValueError("Track source detection index is out of range")
                    check.equal(track["class_id"],raw[raw_index]["class_id"],"track_observed_class",context)
                    check.equal(track["class_name"],raw[raw_index]["class_name"],"track_observed_class_name",context)
                    check.close(track["confidence"],raw[raw_index]["confidence"],"track_observed_confidence",context,tol=1e-6)
                    box=track["box_xyxy"]
                    if not check.check(len(box)==4 and all(isinstance(x,(float,int)) and math.isfinite(x) for x in box)
                                       and box[2]>box[0] and box[3]>box[1],"track_box_finite_positive_area",context):
                        raise ValueError("Invalid track box prevents independent region assignment")
                    zone=assign(track["box_xyxy"],zones)
                    check.equal(track["assigned_zone"],zone,"independent_roi_assignment",context)
                    if zone=="outside_roi":outside+=1
                    else:counts[zone]+=1
                    class_totals[track["class_name"]]+=1
                    info=track_info.setdefault(track_id,{"first_seconds":stamp,"last_end_seconds":end,"last_index":idx-1,"observed_frames":0,"observed_duration_seconds":0.0,"observation_segments":0,"class_changes":0,"last_class":track["class_id"],"zones":set()})
                    if info["observed_frames"]==0 or idx!=info["last_index"]+1:info["observation_segments"]+=1
                    info["class_changes"]+=int(track["class_id"]!=info["last_class"])
                    info.update(last_end_seconds=end,last_index=idx,last_class=track["class_id"])
                    info["observed_frames"]+=1;info["observed_duration_seconds"]+=duration;info["zones"].add(zone)
                totals.update(raw_detections=len(raw),second_resolution_raw_detections=len(second),background_candidates=len(candidates),background_removed=len(bg_remove),cross_class_removed=len(cross_remove),containment_removed=len(removals),final_tracker_input=len(final),current_track_observations=len(ids),unresolved_pair_observations=len(unresolved))
                totals["frames_with_background_candidates"]+=bool(candidates);totals["frames_with_containment_removals"]+=bool(removals);totals["frames_with_unresolved_pairs"]+=bool(unresolved)
                totals["containment_stage_A_removed"]+=sum(x[2]=="A" for x in removals);totals["containment_stage_B_removed"]+=sum(x[2]=="B" for x in removals)
                if unresolved:flag("unresolved_heavy_overlap",frame_index=idx,source_seconds=stamp,pairs=unresolved)
                if len(raw)>=300:flag("raw_max_det_saturation",frame_index=idx,source_seconds=stamp,count=len(raw),resolution=640)
                if len(second)>=300:flag("second_resolution_max_det_saturation",frame_index=idx,source_seconds=stamp,count=len(second),resolution=1280)
                if previous_counts is not None:
                    for name in names:
                        diff=counts[name]-previous_counts[name]
                        if abs(diff)>=3:flag("adjacent_count_jump",frame_index=idx,source_seconds=stamp,zone_name=name,previous=previous_counts[name],current=counts[name],difference=diff)
                if sum(counts.values())==0:
                    if zero_start is None:zero_start=stamp
                    zero_frames+=1
                else:finish_zero(stamp)
                previous_counts=counts
                if sum(counts.values())>peak_total:peak_total,peak_total_time=sum(counts.values()),stamp
                totals["frames_with_no_tracks"]+=not ids;totals["frames_with_no_in_zone_tracks"]+=sum(counts.values())==0
            else:
                totals["invalid_frames"]+=1
                check.equal(row["tracks_primary"],[],"invalid_frame_no_tracks",context)
                check.equal(row["zone_counts"],None,"invalid_frame_counts_not_zero",context)
                check.check(bool(row.get("missing_reason")),"invalid_frame_reason",context)
                finish_zero(stamp);previous_counts=None
                candidates=bg_remove=cross_remove=removals=unresolved=[]
            check.equal(row["zone_counts"],counts,"frame_roi_counts",context)
            check.equal(row["total_in_zones"],sum(counts.values()) if valid else None,"frame_total_count",context)
            check.equal(row["outside_zone_count"],outside if valid else None,"frame_outside_count",context)
            check.equal(row["all_current_tracks"],len(ids) if valid else None,"frame_all_track_count",context)
            check.equal(row["raw_detection_count"],len(raw) if valid else None,"frame_raw_count",context)
            global_stats.add(duration,counts);add_windows(windows,stamp,end,counts,start,stop)
            writer.writerow({"frame_index":idx,"source_timestamp_seconds":stamp,"duration_seconds":duration,"valid":valid,"missing_reason":row.get("missing_reason"),**(counts or {}),"total_in_zones":sum(counts.values()) if valid else None,"outside_zone_count":outside if valid else None,"all_current_tracks":len(ids) if valid else None,"raw_detection_count":len(raw),"second_resolution_raw_count":len(second),"background_candidates":len(candidates),"background_removed":len(bg_remove),"cross_class_removed":len(cross_remove),"containment_removed":len(removals),"unresolved_heavy_pairs":len(unresolved)})
            previous_end=end
        finish_zero(previous_end)
    check.equal(next(time_rows,None),None,"original_pts_all_consumed")
    check.equal(next(timeline,None),None,"timeline_all_consumed")
    check.equal(totals["frames"],expected_count,"all_original_frames_present")
    check.equal(totals["invalid_frames"],0,"all_original_frames_valid")
    check.equal(totals["generated_missing_intervals"],0,"no_missing_intervals")
    check.close(previous_end,stop,"presentation_end_complete")
    check.close(global_stats.valid+global_stats.missing,stop-start,"presentation_duration_conservation",tol=1e-6)
    window_columns=["window_index","source_start_seconds","source_end_seconds_exclusive","duration_seconds","zone_name","mean_current_vehicles","min_current_vehicles","max_current_vehicles","valid_zero_duration_seconds","valid_duration_seconds","missing_duration_seconds","validity","is_tail_window"]
    written_windows=[]
    expected_windows=iter((i,name,s) for i,s in enumerate(windows) for name in names)
    with (out/"window_counts_10s.csv").open("x",newline="",encoding="utf-8-sig") as f:
        writer=csv.DictWriter(f,fieldnames=window_columns);writer.writeheader()
        for emitted in rows(attempt/"windows.jsonl"):
            expected=next(expected_windows,None)
            if expected is None:raise ValueError("Extra production window rows")
            i,name,s=expected;left=start+i*10;right=min(stop,left+10)
            check.equal((emitted["window_index"],emitted["zone_name"]),(i,name),"window_identity")
            expected_values={**s.zone(name),"source_start_seconds":left,"source_end_seconds_exclusive":right,"duration_seconds":right-left,"valid_duration_seconds":s.valid,"missing_duration_seconds":s.missing,"validity":"missing" if s.valid==0 else "partial" if s.missing>1e-9 else "valid","is_tail_window":right-left<10-1e-9}
            check.equal(emitted["valid_frame_intersections"],s.valid_intersections,"window_valid_frame_intersections",{"window":i,"zone":name})
            check.equal(emitted["missing_interval_intersections"],s.missing_intersections,"window_missing_interval_intersections",{"window":i,"zone":name})
            check.close(emitted["valid_fraction"],s.valid/(right-left),"window_valid_fraction",{"window":i,"zone":name},tol=1e-6)
            for key,value in expected_values.items():
                if isinstance(value,float):check.close(emitted[key],value,"independent_window_"+key,{"window":i,"zone":name},tol=1e-6)
                else:check.equal(emitted[key],value,"independent_window_"+key,{"window":i,"zone":name})
            export={"window_index":i,"zone_name":name,**expected_values};export.pop("observed_zero")
            writer.writerow(export);written_windows.append(export)
    check.equal(next(expected_windows,None),None,"all_expected_windows_present")
    for field,expected in (("frame_count",totals["frames"]),("valid_frame_count",totals["valid_frames"]),("invalid_frame_count",totals["invalid_frames"]),("generated_missing_interval_count",totals["generated_missing_intervals"]),("window_count",len(windows)),("frames_with_no_tracks",totals["frames_with_no_tracks"]),("frames_with_no_in_zone_tracks",totals["frames_with_no_in_zone_tracks"])):
        check.equal(summary[field],expected,"summary_"+field)
    check.close(summary["valid_duration_seconds"],global_stats.valid,"summary_valid_duration",tol=1e-6)
    check.close(summary["missing_duration_seconds"],global_stats.missing,"summary_missing_duration",tol=1e-6)
    for name in names:
        for field,value in global_stats.zone(name).items():
            if isinstance(value,float):check.close(summary["zones"][name][field],value,"summary_zone_"+field,name,tol=1e-6)
            else:check.equal(summary["zones"][name][field],value,"summary_zone_"+field,name)
    track_rows=[]
    for tid,info in sorted(track_info.items()):
        track_rows.append({"track_id":tid,"first_seconds":info["first_seconds"],"last_end_seconds":info["last_end_seconds"],"span_seconds":info["last_end_seconds"]-info["first_seconds"],"observed_frames":info["observed_frames"],"observed_duration_seconds":info["observed_duration_seconds"],"observation_segments":info["observation_segments"],"class_changes":info["class_changes"],"zones_observed":"|".join(sorted(info["zones"]))})
    if track_rows:
        with (out/"track_observation_diagnostics.csv").open("x",newline="",encoding="utf-8-sig") as f:
            w=csv.DictWriter(f,fieldnames=list(track_rows[0]));w.writeheader();w.writerows(track_rows)
    report={"schema_version":"selected_camera_frozen_v5_independent_output_audit_v1",
        "created_at":datetime.now(timezone.utc).isoformat(),"status":"passed" if not check.errors else "failed",
        "scope":"selected_Validation_source_engineering_consistency_not_accuracy_evaluation","phase":args.phase,
        "source_id":entry["source_id"],"camera_id":entry["camera_id"],"source_sha256":entry["source_sha256"],
        "model_sha256":MODEL_SHA,"attempt":str(attempt),"selection_sha256":selection_sha,"pts_sha256":time_hash,
        "background_policy_sha256":bg_sha,"background_filter":"none","containment_policy_sha256":ct_sha,
        "audit_script_sha256":sha(__file__),"receipt_files_verified":receipt_verified,"check_count":check.check_count,
        "error_count":sum(check.errors.values()),"error_counts":dict(check.errors),"error_examples":check.examples,
        "selected_source_time_range_seconds":[start,stop],"full_original_source_duration_seconds":preflight["input_duration_seconds"],
        "source_frame_range":[first_index,end_index],"valid_duration_seconds":global_stats.valid,
        "missing_duration_seconds":global_stats.missing,"window_count":len(windows),"window_rows":len(written_windows),
        "tail_window_seconds":stop-(start+(len(windows)-1)*10),"totals":dict(totals),
        "zones":{name:global_stats.zone(name) for name in names},"flag_rules":FLAGS,"flags":dict(flag_counts),
        "observed_track_diagnostics":{"distinct_track_ids":len(track_rows),"not_unique_physical_vehicles":True,
            "tracks_with_observation_gaps":sum(x["observation_segments"]>1 for x in track_rows),
            "tracks_with_class_changes":sum(x["class_changes"]>0 for x in track_rows),
            "single_frame_track_ids":sum(x["observed_frames"]==1 for x in track_rows),
            "median_observed_duration_seconds":statistics.median(x["observed_duration_seconds"] for x in track_rows) if track_rows else None,
            "class_observation_totals_not_vehicle_counts":dict(class_totals)},
        "limitations":["Consistency checks cannot establish detector/count/identity accuracy without independent ground truth.",
            "A flag can be caused by natural entry/exit or occlusion; flags are not labelled errors.",
            "Filter indices and geometric assignments are independently recalculated; detector inference and ByteTrack association are not independently rerun.",
            "Track lifetimes/gaps describe assigned IDs, not true identities or traffic flow.",
            "Counts are current visible observed tracks in selected image regions, not cumulative vehicles, calibrated density, speed, queue length or congestion.",
            "This is development Validation data, not an independent final test result.",
            "No camera-specific background exclusion is used without camera-specific evidence.",
            "Duration-weighted counts follow original displayed-frame intervals; long-frame intervals retain the last observed image and do not imply fresh observations throughout."]+entry.get("limitations",[]),
        "main_test_media_accessed":False,"new_human_annotations":False}
    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig,grid=plt.subplots(len(names),1,figsize=(14,max(4,2.5*len(names))),sharex=True,layout="constrained",squeeze=False)
        axes=grid[:,0]
        colors=["#b89400","#298d3d","#2073b8","#a1459e"]
        for ax,name,color in zip(axes,names,itertools.cycle(colors)):
            selected=[x for x in written_windows if x["zone_name"]==name]
            times=[(x["source_start_seconds"]+x["source_end_seconds_exclusive"])/120 for x in selected]
            ax.plot(times,[x["mean_current_vehicles"] for x in selected],color=color,lw=1.2)
            ax.fill_between(times,[x["min_current_vehicles"] for x in selected],[x["max_current_vehicles"] for x in selected],color=color,alpha=.15,label="10 s min-max")
            ax.set_ylabel(name+"\nvehicles");ax.set_ylim(bottom=0);ax.grid(alpha=.2)
        axes[0].set_title(entry["camera_id"]+" | 10 s duration-weighted current visible vehicle estimates")
        axes[-1].set_xlabel("Original source time (minutes)")
        fig.savefig(out/"current_counts_over_source.png",dpi=170);plt.close(fig)
    report["pts_verification"]={"actual_runtime_path":str(args.pts.resolve()),
        "actual_preflight_bound_sha256":preflight["frame_timing_sha256"],
        "complete_full_original_sidecar_validated_including_unselected_prefix_and_tail":True,
        "selected_frames_compared_exact_integer_ticks":True,
        "normalization":"Explicit pts/pkt_pts and duration/pkt_duration aliases only; conflicting/missing/nonpositive duration fails; DTS never substituted."}
    report["source_image_observation_sampling"]={"decoded_original_images":totals["frames"],
        "selected_presentation_duration_seconds":stop-start,"average_decoded_images_per_presentation_second":totals["frames"]/(stop-start),
        "long_interval_threshold_seconds":.5,"long_presentation_intervals":totals["long_presentation_intervals"],
        "long_presentation_interval_duration_seconds":totals["long_presentation_duration_seconds"],
        "meaning":"Every decoded original image is a fresh model input; between original PTS values the displayed image and its estimate persist. Duration-weighted means include held-image intervals, which add no new visual evidence."}
    report["total_selected_road_current_count"]={
        "mean_current_vehicles":sum(global_stats.zone(name)["mean_current_vehicles"] for name in names) if global_stats.valid else None,
        "max_current_vehicles":peak_total if peak_total>=0 else None,
        "first_peak_source_seconds":peak_total_time,
        "meaning":"Sum across this camera's disjoint selected ROIs at a moment, not cumulative vehicle throughput."}
    check.equal(sha(args.selection),selection_sha,"selection_unchanged_during_audit")
    report.update(status="passed" if not check.errors else "failed",check_count=check.check_count,error_count=sum(check.errors.values()),error_counts=dict(check.errors),error_examples=check.examples)
    report["exports"]={p.name:{"sha256":sha(p),"bytes":p.stat().st_size} for p in out.iterdir() if p.is_file() and p.name!="qc_summary.json"}
    write_json(out/"qc_summary.json",report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("attempt","output","selection","pts"):
        parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--layout",type=Path)
    parser.add_argument("--source-id",required=True)
    parser.add_argument("--phase",choices=("short","full"),required=True)
    parser.add_argument("--plot",action="store_true")
    args=parser.parse_args()
    report=audit_run(args)
    print(json.dumps({k:report[k] for k in ("status","check_count","error_count","totals","flags")},ensure_ascii=False,indent=2))
    return 0 if report["status"]=="passed" else 2


if __name__=="__main__":
    sys.exit(main())
