"""Fail-closed configuration and development input provenance."""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import re
from pathlib import Path

from .common import canonical, load_json, number, resolve, sha256


def load_config(path):
    path = Path(path).resolve()
    config = load_json(path)
    if config.get("schema_version") != 1 or config.get("development_only") is not True:
        raise ValueError("schema_version=1 and development_only=true are mandatory")
    for key in ("run_version", "rules_version"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", config.get(key, "")):
            raise ValueError(f"{key} must be an explicit safe version identifier")
    if config.get("input", {}).get("kind") not in ("pilot_cache", "video"):
        raise ValueError("input.kind must be pilot_cache or video")
    for key in ("split_manifest", "source_root", "camera_registry", "output_root"):
        config[key] = str(resolve(path.parent, config[key]))
    config["model"]["path"] = str(resolve(path.parent, config["model"]["path"]))
    dedup = config.get("dedup", {})
    if "containment_policy_path" in dedup:
        policy_path = resolve(path.parent, dedup["containment_policy_path"])
        if sha256(policy_path) != dedup.get("containment_policy_sha256"):
            raise ValueError("Containment policy SHA256 mismatch")
        from vehicle_containment import validate_policy
        policy = load_json(policy_path)
        validate_policy(policy)
        dedup["containment_policy_path"] = str(policy_path)
        dedup["containment_policy"] = policy
    for key in ("audit_summary_path", "frame_timing_path"):
        if key in config.get("time", {}):
            config["time"][key] = str(resolve(path.parent, config["time"][key]))
    if "inference_enabled" in config and type(config["inference_enabled"]) is not bool:
        raise ValueError("inference_enabled must be boolean")
    if not re.fullmatch(r"[0-9a-f]{64}", config["model"].get("sha256", "")) or not config["model"].get("version"):
        raise ValueError("Explicit model version and SHA256 required")
    inp = config["input"]
    if inp["kind"] == "pilot_cache":
        for key in ("records", "manifest", "historical_config", "historical_status", "historical_summary"):
            inp[key] = str(resolve(path.parent, inp[key]))
    elif inp.get("path"):
        raise ValueError("Video inputs use source_root/camera/source_id; arbitrary path substitution is forbidden")
    if inp["kind"] == "video" and number(inp.get("source_offset_seconds", 0), "input.source_offset_seconds") != 0:
        raise ValueError("Original-video manifest inputs must have source_offset_seconds=0; clip offsets require explicit clip provenance")
    segment = config.get("segment")
    if segment is not None:
        if not isinstance(segment, dict) or set(segment) - {"start_seconds", "end_seconds"}:
            raise ValueError("segment accepts only source-time start_seconds/end_seconds")
        for key, value in segment.items():
            if number(value, f"segment.{key}") < 0:
                raise ValueError("segment times must be nonnegative")
    if type(config.get("progress_every_frames", 300)) is not int or config.get("progress_every_frames", 300) <= 0:
        raise ValueError("progress_every_frames must be a positive integer")
    agg = config["aggregation"]
    if number(agg["window_seconds"], "window_seconds") <= 0:
        raise ValueError("window_seconds must be positive")
    if type(agg["chunk_frames"]) is not int or not 1 <= agg["chunk_frames"] <= 100000:
        raise ValueError("chunk_frames must be integer 1..100000")
    return config


def registry_entries(config):
    registry = load_json(config["camera_registry"])
    if registry.get("schema_version") != 1 or not isinstance(registry.get("cameras"), dict):
        raise ValueError("camera registry requires schema_version=1 and cameras object")
    return registry["cameras"]


def layout_identity(config, task, layout):
    """Bind geometry to a camera independently of the outer camera wrapper.

    A legacy reference filename is resolved from the frozen CSV metadata only;
    no referenced image, video, label or prediction is opened. The reference
    source may differ from the task source when both belong to the same camera.
    """
    if not isinstance(layout, dict):
        raise ValueError("LAYOUT_IDENTITY_UNVERIFIED: layout must be an object")
    explicit = layout.get("camera_id")
    reference = layout.get("source_video")
    if "camera_id" in layout and (not isinstance(explicit, str) or not explicit.strip()):
        raise ValueError("LAYOUT_IDENTITY_UNVERIFIED: layout.camera_id must be a nonempty string")
    if "source_video" in layout and (not isinstance(reference, str) or not reference.strip()):
        raise ValueError("LAYOUT_IDENTITY_UNVERIFIED: layout.source_video must name a frozen reference source")
    reference_camera = None
    reference_name = None
    if reference is not None:
        if not config.get("split_manifest"):
            raise ValueError("LAYOUT_IDENTITY_UNVERIFIED: source_video requires the configured frozen split_manifest")
        from .batch import list_development
        manifest = Path(config["split_manifest"])
        manifest_sha = sha256(manifest)
        list_development(manifest)  # Validate all frozen memberships, including CSV-only Main Test rows.
        reference_name = Path(reference).name
        with manifest.open(encoding="utf-8-sig", newline="") as handle:
            row = next((r for r in csv.DictReader(handle) if r["Source Video Filename"] == reference_name), None)
        if sha256(manifest) != manifest_sha:
            raise ValueError("LAYOUT_IDENTITY_UNVERIFIED: frozen manifest changed during identity lookup")
        if row is None:
            raise ValueError(f"LAYOUT_IDENTITY_UNVERIFIED: reference source is absent from frozen manifest: {reference_name}")
        for owner, declared in (("configuration", config.get("split_manifest_sha256")),
                                ("layout", layout.get("split_manifest_sha256"))):
            if declared is not None and declared != manifest_sha:
                raise ValueError(f"LAYOUT_IDENTITY_CONFLICT: {owner} split manifest SHA256 mismatch")
        if "source_split" in layout and layout["source_split"] != row["Formal Split"]:
            raise ValueError("LAYOUT_IDENTITY_CONFLICT: layout.source_split differs from its reference source split")
        reference_camera = row["Intersection"]
    if explicit is not None and reference_camera is not None and explicit != reference_camera:
        raise ValueError(f"LAYOUT_IDENTITY_CONFLICT: camera_id={explicit}, reference source camera={reference_camera}")
    resolved = explicit if explicit is not None else reference_camera
    if resolved is None:
        raise ValueError("LAYOUT_IDENTITY_UNVERIFIED: layout needs camera_id or a resolvable frozen source_video")
    if resolved != task["camera_id"]:
        raise ValueError(f"LAYOUT_CAMERA_MISMATCH: layout camera={resolved}, task camera={task['camera_id']}")
    return {"camera_id": resolved, "reference_source_id": reference_name,
            "basis": "explicit_camera_id_and_frozen_reference" if explicit and reference_camera else
                     "explicit_camera_id" if explicit else "frozen_reference_source",
            "reference_media_opened": False}


def _applicability_evidence(config, task, camera, camera_path):
    """Bind declared review files without opening their referenced media.

    Legacy clip configurations have no evidence array. Once an array is
    declared it must be nonempty, intact and, for JSON records, source-scoped.
    """
    if "applicability_evidence" not in camera:
        return {}
    entries = camera["applicability_evidence"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("ROI_EVIDENCE_INVALID: applicability_evidence must be a nonempty list")
    hashes = {}
    for entry in entries:
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                or not entry["path"].strip() or not isinstance(entry.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise ValueError("ROI_EVIDENCE_INVALID: each entry requires a path and SHA256")
        evidence_path = resolve(camera_path.parent, entry["path"])
        try:
            payload = evidence_path.read_bytes()
        except OSError as error:
            raise ValueError(f"ROI_EVIDENCE_UNAVAILABLE: {evidence_path}") from error
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise ValueError(f"ROI_EVIDENCE_CHANGED: SHA256 mismatch: {evidence_path}")
        hashes[str(evidence_path)] = entry["sha256"]
        if evidence_path.suffix.lower() != ".json":
            continue
        try:
            record = json.loads(payload)
        except (ValueError, UnicodeError) as error:
            raise ValueError(f"ROI_EVIDENCE_INVALID: invalid JSON: {evidence_path}") from error
        if not isinstance(record, dict):
            raise ValueError(f"ROI_EVIDENCE_INVALID: JSON record must be an object: {evidence_path}")
        # These are the supported identity containers in plan, extraction,
        # review and timing-audit records. Check every declaration, not just
        # the first matching source, so contradictory nested scopes fail.
        identities = [record]
        for key in ("plan", "task"):
            if key in record:
                if not isinstance(record[key], dict):
                    raise ValueError(f"ROI_EVIDENCE_INVALID: {key} must be an object: {evidence_path}")
                identities.append(record[key])
        found_camera = found_source = False
        for identity in identities:
            if "camera_id" in identity:
                if identity["camera_id"] != task["camera_id"]:
                    raise ValueError(f"ROI_EVIDENCE_SCOPE_MISMATCH: camera_id: {evidence_path}")
                found_camera = True
            for key in ("source", "source_id", "source_video", "source_video_filename"):
                if key in identity:
                    value = identity[key]
                    if not isinstance(value, str) or Path(value).name != task["source_id"]:
                        raise ValueError(f"ROI_EVIDENCE_SCOPE_MISMATCH: {key}: {evidence_path}")
                    found_source = True
            for key in ("split", "source_split"):
                if key in identity and identity[key] != task["split"]:
                    raise ValueError(f"ROI_EVIDENCE_SCOPE_MISMATCH: {key}: {evidence_path}")
            for key in ("manifest_sha256", "split_manifest_sha256"):
                if key in identity:
                    manifest_sha = config.get("split_manifest_sha256") or sha256(config["split_manifest"])
                    if identity[key] != manifest_sha:
                        raise ValueError(f"ROI_EVIDENCE_SCOPE_MISMATCH: {key}: {evidence_path}")
        if not found_camera or not found_source:
            raise ValueError(f"ROI_EVIDENCE_SCOPE_UNVERIFIED: JSON requires camera and source identity: {evidence_path}")
    return hashes


def camera_for(config, task):
    entry = registry_entries(config).get(task["camera_id"])
    if not entry or not entry.get("config_path"):
        raise ValueError(f"MISSING_CAMERA_CONFIG: {task['camera_id']}; prepare its own geometry/applicability, no Newport fallback")
    path = resolve(Path(config["camera_registry"]).parent, entry["config_path"])
    camera = load_json(path)
    if camera.get("camera_id") != task["camera_id"] or task["source_id"] not in camera.get("source_ids", []):
        raise ValueError("CAMERA_SCOPE_MISMATCH: source not explicitly listed for this camera layout")
    if not camera.get("roi_version") or not camera.get("applicability_basis"):
        raise ValueError("ROI version and applicability evidence are required")
    camera["applicability_evidence_hashes"] = _applicability_evidence(config, task, camera, path)
    layout_path = resolve(path.parent, camera["layout_path"])
    camera["layout"] = load_json(layout_path)
    camera["layout_identity"] = layout_identity(config, task, camera["layout"])
    from roi_geometry import validate_layout
    validate_layout(camera["layout"])
    camera["layout_path"] = str(layout_path)
    camera["layout_sha256"] = sha256(layout_path)
    camera["config_path"] = str(path)
    policy = camera.get("background_filter")
    if not isinstance(policy, dict) or policy.get("mode") not in ("none", "cached_policy_only", "live_second_resolution"):
        raise ValueError("Explicit supported camera.background_filter mode required")
    if policy["mode"] != "none":
        policy_path = resolve(path.parent, policy["policy_path"])
        policy["policy_path"] = str(policy_path)
        policy["policy"] = load_json(policy_path)
        actual_sha = sha256(policy_path)
        if (policy["mode"] == "live_second_resolution" or "policy_sha256" in policy) and policy.get("policy_sha256") != actual_sha:
            raise ValueError("Background policy SHA256 mismatch")
        policy["policy_sha256"] = actual_sha
        from confirm_background_candidates import validate_policy
        validate_policy(policy["policy"])
        p = policy["policy"]
        if p["camera_id"] != task["camera_id"] or p["source_video_filename"] != task["source_id"]:
            raise ValueError("Camera-specific background policy scope mismatch")
        if (p["frame_width"], p["frame_height"]) != (camera["layout"]["frame_width"], camera["layout"]["frame_height"]):
            raise ValueError("Background policy dimensions mismatch")
    return camera


def check_scope(camera, start, end):
    intervals = camera.get("allowed_source_intervals_seconds", [])
    if not any(len(pair) == 2 and number(pair[0], "scope start") <= start and end <= number(pair[1], "scope end") for pair in intervals):
        raise ValueError(f"ROI_SCOPE_NOT_VERIFIED: requested [{start},{end}) outside declared camera intervals {intervals}; record AI applicability checks before extending")


def code_fingerprints():
    root = Path(__file__).parent
    files = list(root.glob("*.py")) + [root.parent / name for name in (
        "roi_geometry.py", "replay_vehicle_tracking_pilot.py", "run_video_tracking_pilot.py", "confirm_background_candidates.py",
        "vehicle_containment.py")]
    return {str(p): sha256(p) for p in sorted(files)}


def prepare(config_path, source_id):
    from .batch import list_development
    config = load_config(config_path)
    # Resolve frozen membership BEFORE reading source/cache/model/image data.
    tasks = list_development(Path(config["split_manifest"]))
    task = next((row for row in tasks if row["source_id"] == source_id), None)
    if task is None:
        raise ValueError("SOURCE_NOT_DEVELOPMENT: only frozen 81 Train + 17 Validation are executable; Main Test is blocked")
    task = dict(task)
    task["input_path"] = str(Path(config["source_root"]) / task["camera_id"] / source_id)
    config["split_manifest_sha256"] = sha256(config["split_manifest"])
    if config["input"].get("source_id", source_id) != source_id:
        raise ValueError("Input cache explicitly belongs to a different development source")
    camera = camera_for(config, task)
    if config["input"]["kind"] == "pilot_cache":
        from .cache import preflight_cache
        preflight = preflight_cache(config, task, camera)
    else:
        # Cheap applicability check before any expensive PTS audit/model access.
        segment = config.get("segment") or {}
        duration = task["manifest_duration_s"]
        # The CSV duration is nominal. Whole-input scopes are checked against
        # audited timing below, not rounded manifest HH:MM:SS metadata.
        if segment:
            check_scope(camera, segment.get("start_seconds", 0), segment.get("end_seconds", duration))
        from .video import preflight_video
        preflight = preflight_video(config, task, camera)
    check_scope(camera, preflight["source_start_seconds"], preflight["source_end_seconds"])
    files = {config["split_manifest"], config["camera_registry"], camera["config_path"], camera["layout_path"]}
    files.update(camera["applicability_evidence_hashes"])
    if config["input"]["kind"] == "pilot_cache":
        files.update(config["input"][k] for k in ("records", "manifest", "historical_config", "historical_status", "historical_summary"))
    else:
        files.update((task["input_path"], config["model"]["path"]))
        if config.get("time", {}).get("mode") == "audited_pts":
            files.update(config["time"][key] for key in ("audit_summary_path", "frame_timing_path"))
            files.update(preflight["audited_evidence_files"])
    if camera["background_filter"]["mode"] != "none":
        files.add(camera["background_filter"]["policy_path"])
    if config.get("dedup", {}).get("containment_policy_path"):
        files.add(config["dedup"]["containment_policy_path"])
    inputs = {str(p): sha256(p) for p in sorted(files)}
    for path, digest in camera["applicability_evidence_hashes"].items():
        if inputs[path] != digest:
            raise ValueError(f"ROI_EVIDENCE_CHANGED: evidence changed during preflight: {path}")
    implementation = code_fingerprints()
    runtime = {}
    for distribution in ("numpy", "opencv-python", "ultralytics", "torch"):
        try:
            runtime[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            runtime[distribution] = "not_installed"
    stable_preflight = {k: v for k, v in preflight.items() if k != "preflight_elapsed_seconds"}
    fingerprint = hashlib.sha256(canonical({"config": config, "camera": camera, "task": task,
        "preflight": stable_preflight, "inputs": inputs, "implementation": implementation, "runtime": runtime}).encode()).hexdigest()
    metadata = {
        "source": source_id, "source_id": source_id, "split": task["split"], "camera_id": task["camera_id"],
        "run": config["run_version"], "model_version": config["model"]["version"],
        "model_sha256": config["model"]["sha256"], "roi_version": camera["roi_version"],
        "roi_sha256": camera["layout_sha256"], "rules_version": config["rules_version"],
        "configuration_sha256": fingerprint, "synthetic": False,
        "method_status": config.get("method_status", "development_candidate_not_frozen"),
        "inference_enabled": config.get("inference_enabled", True),
        "time_mode": preflight["time_mode"], "input_source_offset_seconds": preflight["input_source_offset_seconds"],
        "frame_index_origin": "input_file",
        "frame_index_semantics": "zero-based decoded input index; for transcoded clips this is not the original video frame number",
    }
    return {"config": config, "task": task, "camera": camera, "preflight": preflight,
            "fingerprint": fingerprint, "run_version": config["run_version"], "metadata": metadata,
            "input_hashes": inputs, "implementation_hashes": implementation, "runtime": runtime}
