"""Read historical pilot tracks without inference, retracking or new labels.

Only the explicitly documented CFR clip contract is accepted. Historical frame
times are checked, not silently replaced by frame_index / 30.
"""
from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path

from .common import load_json, number, sha256


def preflight_cache(config, task, camera):
    inp = config["input"]
    manifest = load_json(inp["manifest"])
    identity = manifest.get("source_split", {})
    if (identity.get("Source Video Filename"), identity.get("Formal Split"), identity.get("Intersection")) != (
        task["source_id"], task["split"], task["camera_id"]
    ):
        raise ValueError("Cache manifest source/split/camera differs from frozen development task")
    if manifest.get("split_manifest_sha256") != config["split_manifest_sha256"]:
        raise ValueError("Cache manifest does not bind the frozen split CSV")
    historical = load_json(inp["historical_config"])
    if historical.get("source_split") != identity:
        raise ValueError("Historical config and clip manifest identities differ")
    if historical.get("source", {}).get("sha256") != manifest.get("clip_sha256"):
        raise ValueError("Historical cache clip hash mismatch")
    for field in ("width", "height", "frame_count", "fps", "clip_start_seconds", "clip_end_seconds"):
        if historical.get(field) != manifest.get(field):
            raise ValueError(f"Historical cache {field} mismatch")
    if "CFR" not in manifest.get("processing", "") or not historical.get("time_semantics", "").startswith("CFR"):
        raise ValueError("Cache needs explicit historical CFR evidence; unknown/VFR time mode is unsupported")
    fps = Fraction(str(manifest["fps"]))
    count = manifest["frame_count"]
    if fps <= 0 or type(count) is not int or count <= 0:
        raise ValueError("Invalid cache fps/frame count")
    start = number(manifest["clip_start_seconds"], "clip start")
    end = number(manifest["clip_end_seconds"], "clip end")
    if start < 0 or not math.isclose(end-start, float(count/fps), abs_tol=1e-7, rel_tol=0):
        raise ValueError("Cache duration/frame count inconsistent")
    if (manifest["width"], manifest["height"]) != (camera["layout"]["frame_width"], camera["layout"]["frame_height"]):
        raise ValueError("Cache dimensions differ from camera ROI; implicit resizing is forbidden")
    if historical["model"]["sha256"] != config["model"]["sha256"]:
        raise ValueError("Cache model cannot be changed without fresh inference")
    old_detector = {k: v for k, v in historical["predict_settings"].items() if k not in ("device", "save", "verbose")}
    if config["detector"] != old_detector or config["tracker"] != historical["tracker_settings_both_arms"]:
        raise ValueError("Cache detector/tracker parameters are historical; changing them requires fresh inference")
    old_dedup = historical.get("duplicate_suppression")
    wanted = {"mode": "cross_class_greedy_iou", "iou_threshold": old_dedup["iou_threshold"]} if old_dedup else {"mode": "none"}
    containment_keys = {"containment_policy_path", "containment_policy_sha256", "containment_policy"}
    if {k: v for k, v in config["dedup"].items() if k not in containment_keys} != wanted:
        raise ValueError("Cached dedup differs from requested method")
    contained = historical.get("contained_vehicle_suppression")
    if contained:
        requested = config["dedup"]
        if wanted != {"mode": "cross_class_greedy_iou", "iou_threshold": .7}:
            raise ValueError("Cached frozen containment requires preceding cross-class dedup at IoU 0.7")
        if (not containment_keys <= requested.keys() or
                requested["containment_policy_sha256"] != contained.get("policy", {}).get("sha256") or
                requested["containment_policy"] != contained.get("parameters")):
            raise ValueError("Cached containment requires its explicit matching frozen policy")
        from .video import _bound_policy
        from vehicle_containment import validate_policy
        validate_policy(_bound_policy(requested, "containment_policy_path", "containment_policy_sha256", "containment_policy"))
    elif containment_keys & config["dedup"].keys():
        raise ValueError("Requested containment was not applied to these cached tracks")
    background = historical.get("background_confirmation")
    policy = camera["background_filter"]
    if background:
        if policy.get("mode") != "cached_policy_only" or policy.get("policy_sha256") != background["policy"]["sha256"]:
            raise ValueError("Cached background selection requires explicit matching camera policy")
        if policy["policy"]["source_clip_sha256"] != manifest["clip_sha256"]:
            raise ValueError("Background filter belongs to a different clip")
    elif policy.get("mode") != "none":
        raise ValueError("Requested background filter was not applied to these cached tracks")
    status = load_json(inp["historical_status"])
    if status.get("state") != "complete" or status.get("processed_frames") != count:
        raise ValueError("Historical cache is not a completed frame sequence")
    summary = load_json(inp["historical_summary"])
    records_hash = sha256(inp["records"])
    if records_hash != summary.get("frames_file", {}).get("sha256"):
        raise ValueError("Historical cache frames hash differs from completed summary")
    segment = config.get("segment") or {}
    selected_start = number(segment.get("start_seconds", start), "segment start")
    selected_end = number(segment.get("end_seconds", end), "segment end")
    if not start <= selected_start < selected_end <= end:
        raise ValueError("Selected range is outside cached clip")
    indices = [(value-start)*float(fps) for value in (selected_start, selected_end)]
    if any(abs(value-round(value)) > 1e-6 for value in indices):
        raise ValueError("Development segment boundaries must align with CFR frames")
    return {
        "width": manifest["width"], "height": manifest["height"], "fps": float(fps),
        "fps_num": fps.numerator, "fps_den": fps.denominator,
        "input_frame_count": count, "frame_count": round(indices[1])-round(indices[0]),
        "first_frame_index": round(indices[0]), "input_source_offset_seconds": start,
        "source_start_seconds": selected_start, "source_end_seconds": selected_end,
        "input_sha256": records_hash, "model_sha256": config["model"]["sha256"],
        "time_mode": "historical_declared_cfr_checked_per_record",
        "time_evidence_limit": "Historical encoded clip provenance plus complete cache timestamp checks; no new video PTS audit",
        "versions": historical.get("versions", {}),
        "original_inference_versions": historical.get("original_inference_versions", {}),
    }


def iter_cache(config, task, camera, preflight):
    inp = config["input"]
    fps = Fraction(preflight["fps_num"], preflight["fps_den"])
    offset = preflight["input_source_offset_seconds"]
    begin = preflight["first_frame_index"]
    stop = begin + preflight["frame_count"]
    count = 0
    with Path(inp["records"]).open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            import json
            row = json.loads(line)
            if type(row.get("frame_index")) is not int or row["frame_index"] != index or index >= preflight["input_frame_count"]:
                raise ValueError(f"Cache missing/duplicate/extra frame at line {index+1}")
            relative = float(index/fps)
            for key, expected in (("timestamp_seconds", relative), ("source_timestamp_seconds", offset+relative)):
                if not math.isclose(number(row.get(key), key), expected, rel_tol=0, abs_tol=1e-7):
                    raise ValueError(f"Cache timestamp mismatch at frame {index}: {key}")
            policy = camera["background_filter"]
            bg = row.get("background_confirmation")
            if policy["mode"] == "cached_policy_only":
                if not bg or bg.get("policy_sha256") != policy["policy_sha256"]:
                    raise ValueError("Per-frame background provenance mismatch")
            elif bg is not None:
                raise ValueError("Unexpected background selection on cached frame")
            contained = row.get("contained_vehicle_suppression")
            requested_containment = config["dedup"].get("containment_policy_sha256")
            if requested_containment is not None:
                if not contained or contained.get("policy_sha256") != requested_containment:
                    raise ValueError("Per-frame containment provenance mismatch")
            elif contained is not None:
                raise ValueError("Unexpected containment selection on cached frame")
            if not isinstance(row.get("tracks_primary"), list) or not isinstance(row.get("raw_detections"), list):
                raise ValueError("Cache needs complete current tracks and raw detection arrays")
            count += 1
            if begin <= index < stop:
                yield {**row, "input_frame_index": index,
                       "timestamp_seconds": row["source_timestamp_seconds"]-preflight["source_start_seconds"],
                       "duration_seconds": float(1/fps), "valid": True,
                       "quality": {"source": "historical_development_cache", "synthetic": False}}
    if count != preflight["input_frame_count"]:
        raise ValueError(f"Cache ended early: {count}/{preflight['input_frame_count']}; incomplete is not zero traffic")
