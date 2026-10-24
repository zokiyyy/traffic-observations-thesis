"""CPU-only, explicitly planned development-source ROI composition evidence.

This helper never loads a detector or labels. Sampling uses decoder time seeking;
the decoder-reported position is evidence, not an unverified frame_index / 30.
The plan is persisted before extraction. Existing evidence is not overwritten.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import cv2
import numpy as np

SYS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SYS_ROOT / "src"))
from roi_geometry import validate_layout


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write("\n")


def planned_times(duration):
    if not math.isfinite(duration) or duration <= 3301:
        raise ValueError("This explicit sample plan requires duration > 3301 seconds.")
    return [float(v) for v in range(0, 3301, 300)] + [duration - 1.0]


def draw_overlay(frame, layout):
    marked = frame.copy()
    fill = frame.copy()
    for zone in layout["zones"]:
        points = np.asarray(zone["polygon_points"], dtype=np.int32)
        cv2.fillPoly(fill, [points], tuple(zone["color_bgr"]))
    marked = cv2.addWeighted(marked, 0.85, fill, 0.15, 0)
    for zone in layout["zones"]:
        points = np.asarray(zone["polygon_points"], dtype=np.int32)
        cv2.polylines(marked, [points], True, tuple(zone["color_bgr"]), 2)
        anchor = tuple(zone["label_anchor"])
        cv2.putText(marked, zone["zone_name"], anchor, cv2.FONT_HERSHEY_SIMPLEX,
                    0.52, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(marked, zone["zone_name"], anchor, cv2.FONT_HERSHEY_SIMPLEX,
                    0.52, tuple(zone["color_bgr"]), 1, cv2.LINE_AA)
    return marked


def contact_sheet(items, output, field):
    width, height, label_h = 640, 360, 32
    for page_start in range(0, len(items), 6):
        page = items[page_start:page_start + 6]
        rows = math.ceil(len(page) / 2)
        sheet = np.full((rows * (height + label_h), width * 2, 3), 240, np.uint8)
        for n, item in enumerate(page):
            img = cv2.imread(str(output / item[field]))
            thumb = cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)
            y = (n // 2) * (height + label_h)
            x = (n % 2) * width
            sheet[y:y + height, x:x + width] = thumb
            text = (f"requested={item['requested_source_seconds']:.3f}s  "
                    f"decoded={item['opencv_pos_msec_after_read'] / 1000:.3f}s  "
                    f"idx={item['decoded_frame_index']:.0f}")
            cv2.putText(sheet, text, (x + 5, y + height + 21), cv2.FONT_HERSHEY_SIMPLEX,
                        0.47, (20, 20, 20), 1, cv2.LINE_AA)
        name = f"contact_{field}_{page_start // 6 + 1:02d}.jpg"
        if not cv2.imwrite(str(output / name), sheet, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise RuntimeError(f"Cannot write {name}")


def screen_source(source, layout, output, duration):
    """Low-cost camera-composition screen, deliberately not traffic analysis."""
    folder = output / "alignment_screen"
    folder.mkdir(exist_ok=False)
    requested = [float(s) for s in range(0, math.floor(duration), 60)] + [duration - 1]
    write_new(folder / "screen_plan.json", {
        "requested_source_seconds": requested,
        "plan_order": "persisted before screen extraction",
        "method": "ORB on complement of existing road ROIs, RANSAC partial affine to time 0",
        "review_trigger": "fewer than 20 inliers, fit failure, or corner displacement > 12 source pixels",
        "interpretation": "Heuristic sparse camera-shift locator, not a calibrated guarantee or a vehicle detector.",
    })
    cv2.setNumThreads(1)
    cap = cv2.VideoCapture(str(source))
    mask = np.full((layout["frame_height"], layout["frame_width"]), 255, np.uint8)
    for z in layout["zones"]:
        cv2.fillPoly(mask, [np.asarray(z["polygon_points"], np.int32)], 0)
    # Dilate the exclusion to reduce traffic leakage near existing ROI borders;
    # this mask is only for image alignment and never replaces the traffic ROI.
    mask = cv2.erode(mask, np.ones((31, 31), np.uint8))
    mask[-35:, :] = 0  # changing burnt-in wall-clock strip
    orb = cv2.ORB_create(nfeatures=2500)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    ref_key, ref_des = None, None
    rows, start = [], time.perf_counter()
    for index, seconds in enumerate(requested):
        if not cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000):
            raise RuntimeError(f"Screen seek failed at {seconds}s")
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"Screen read failed at {seconds}s")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        key, des = orb.detectAndCompute(gray, mask)
        if ref_key is None:
            ref_key, ref_des = key, des
        pairs = matcher.knnMatch(des, ref_des, k=2) if des is not None and ref_des is not None else []
        good = [a for pair in pairs if len(pair) == 2 for a, b in [pair] if a.distance < 0.75 * b.distance]
        affine, inliers = None, None
        if len(good) >= 4:
            a = np.float32([key[m.queryIdx].pt for m in good])
            b = np.float32([ref_key[m.trainIdx].pt for m in good])
            affine, inliers = cv2.estimateAffinePartial2D(a, b, method=cv2.RANSAC,
                            ransacReprojThreshold=2, maxIters=2000, confidence=0.99)
        n_inliers = int(inliers.sum()) if inliers is not None else 0
        width, height = layout["frame_width"], layout["frame_height"]
        corners = np.float64([[0, 0, 1], [width, 0, 1], [width, height, 1], [0, height, 1]])
        max_shift = float(np.max(np.linalg.norm(corners @ affine.T - corners[:, :2], axis=1))) if affine is not None else None
        flagged = max_shift is None or n_inliers < 20 or max_shift > 12
        item = {"sample_index": index, "requested_source_seconds": seconds,
                "opencv_pos_msec_after_read": cap.get(cv2.CAP_PROP_POS_MSEC),
                "decoded_frame_index": cap.get(cv2.CAP_PROP_POS_FRAMES) - 1,
                "good_matches": len(good), "inlier_count": n_inliers,
                "partial_affine_to_first": affine.tolist() if affine is not None else None,
                "maximum_fitted_corner_displacement_pixels": max_shift,
                "static_mask_mean_luminance": float(gray[mask > 0].mean()),
                "needs_visual_review": flagged,
                "thumbnail": f"screen_{index:03d}.jpg"}
        cv2.imwrite(str(folder / item["thumbnail"]), cv2.resize(frame, (640, 360)),
                    [cv2.IMWRITE_JPEG_QUALITY, 90])
        rows.append(item)
    cap.release()
    contact_sheet(rows, folder, "thumbnail")
    result = {"source": str(source.resolve()), "sample_count": len(rows),
              "elapsed_seconds": time.perf_counter() - start,
              "flagged_count": sum(x["needs_visual_review"] for x in rows),
              "samples": rows,
              "limitations": ["60-second samples cannot exclude brief intervening changes.",
                              "Masked ORB/RANSAC can fail due to texture, traffic, lighting; thresholds are review triggers, not accuracy scores.",
                              "Only AI sparse composition review; no human labels or detector inference."]}
    write_new(folder / "screen_evidence.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "samples"}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--layout", type=Path, required=True)
    p.add_argument("--duration-seconds", type=float, required=True)
    p.add_argument("--duration-evidence", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--screen-only", action="store_true")
    args = p.parse_args()
    with args.manifest.open(encoding="utf-8-sig", newline="") as f:
        matches = [r for r in csv.DictReader(f) if r["Source Video Filename"] == args.source.name]
    if len(matches) != 1 or matches[0]["Formal Split"] not in {"Train", "Validation"}:
        raise ValueError("Exactly one frozen development source is required; Main Test is forbidden.")
    row = matches[0]
    layout = json.loads(args.layout.read_text())
    validate_layout(layout)
    camera = layout.get("camera_id")
    if camera is None:
        with args.manifest.open(encoding="utf-8-sig", newline="") as f:
            bound = [r for r in csv.DictReader(f) if r["Source Video Filename"] == layout.get("source_video")]
        camera = bound[0]["Intersection"] if len(bound) == 1 else None
    if camera != row["Intersection"]:
        raise ValueError("Layout camera identity must match source camera.")
    times = planned_times(args.duration_seconds)
    if args.screen_only:
        existing = json.loads((args.output / "sample_plan.json").read_text())
        if (existing["source"] != str(args.source.resolve())
                or existing["layout_sha256"] != digest(args.layout)
                or existing["duration_seconds"] != args.duration_seconds):
            raise ValueError("Screen inputs must match preserved extraction plan.")
        screen_source(args.source, layout, args.output, args.duration_seconds)
        return
    args.output.mkdir(parents=True, exist_ok=False)
    plan = {
        "kind": "AI_assisted_sparse_composition_check_no_manual_annotation",
        "source": str(args.source.resolve()), "source_split": row["Formal Split"],
        "camera_id": row["Intersection"], "manifest_sha256": digest(args.manifest),
        "layout": str(args.layout.resolve()), "layout_sha256": digest(args.layout),
        "layout_version": layout.get("layout_version"),
        "requested_source_seconds": times,
        "plan_order": "persisted before reading or viewing sampled frames",
        "duration_seconds": args.duration_seconds, "duration_evidence": args.duration_evidence,
        "time_warning": "Decoder positions are reported, not inferred from nominal 30 FPS; root full PTS audit independently verifies time.",
        "no_inference": True, "no_ground_truth_created": True,
    }
    write_new(args.output / "sample_plan.json", plan)
    cv2.setNumThreads(1)
    cap = cv2.VideoCapture(str(args.source))
    if not cap.isOpened():
        raise ValueError("Cannot open source with existing OpenCV decoder.")
    meta = {"backend": cap.getBackendName(),
            "reported_fps": cap.get(cv2.CAP_PROP_FPS),
            "reported_frame_count": cap.get(cv2.CAP_PROP_FRAME_COUNT),
            "reported_width": cap.get(cv2.CAP_PROP_FRAME_WIDTH),
            "reported_height": cap.get(cv2.CAP_PROP_FRAME_HEIGHT)}
    items = []
    start = time.perf_counter()
    for n, seconds in enumerate(times):
        t0 = time.perf_counter()
        set_ok = cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, frame = cap.read()
        if not set_ok or not ok or frame is None:
            raise RuntimeError(f"Seeking/decoding failed for requested {seconds:.6f}s")
        h, w = frame.shape[:2]
        if (w, h) != (layout["frame_width"], layout["frame_height"]):
            raise ValueError(f"Decoded dimensions {w}x{h} differ from layout.")
        item = {"sample_index": n, "requested_source_seconds": seconds,
                "opencv_pos_msec_after_read": cap.get(cv2.CAP_PROP_POS_MSEC),
                "opencv_pos_frames_after_read": cap.get(cv2.CAP_PROP_POS_FRAMES),
                "decoded_frame_index": cap.get(cv2.CAP_PROP_POS_FRAMES) - 1,
                "width": w, "height": h, "decode_seconds": time.perf_counter() - t0,
                "decoded_bgr_sha256": hashlib.sha256(frame.tobytes()).hexdigest(),
                "original": f"sample_{n:02d}_original.png",
                "overlay": f"sample_{n:02d}_overlay.png"}
        for key, img in (("original", frame), ("overlay", draw_overlay(frame, layout))):
            if not cv2.imwrite(str(args.output / item[key]), img):
                raise RuntimeError(f"Cannot save {item[key]}")
            item[f"{key}_sha256"] = digest(args.output / item[key])
        items.append(item)
    cap.release()
    for field in ("original", "overlay"):
        contact_sheet(items, args.output, field)
    report = {"plan": plan, "opencv_metadata": meta, "geometry_validation": "passed",
              "all_sample_dimensions_match": True, "samples": items,
              "elapsed_seconds": time.perf_counter() - start,
              "layout_short_clip_metadata": {k: layout.get(k) for k in
                   ("clip_file", "fps", "frame_count", "reference_frame_index", "reference_time_seconds")},
              "limitations": ["13 sparse frames do not rule out changes between samples.",
                              "ROI overlays do not evaluate detector counts, trajectories or traffic state.",
                              "Legacy fps/frame_count/reference fields describe the 1600-1720s pilot clip, not full source."]}
    write_new(args.output / "extraction_evidence.json", report)
    print(json.dumps({"output": str(args.output), "sample_count": len(items),
                      "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
