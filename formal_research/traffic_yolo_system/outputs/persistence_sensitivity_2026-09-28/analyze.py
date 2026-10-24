"""Compare 20/30/60 s persistence using immutable five-source 10 s states."""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent / "five_intersection_states_2026-09-28_v1"
CAMERAS = ("Newport", "NE12th", "Eastgate", "SE38th", "NE8th")
LIMITS = (20, 30, 60)
TOL = 1e-7


def read_json(path: Path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def calculate(camera: str, data: dict, limit: int):
    zones = [z["id"] for z in data["zones"]]
    streak = {z: [0.0, 0.0] for z in zones}
    prior = {z: {"high": False, "joint": False} for z in zones}
    totals = {z: {"high_windows": 0, "joint_windows": 0,
                  "high_seconds": 0.0, "joint_seconds": 0.0,
                  "high_episodes": 0, "joint_episodes": 0,
                  "eligible_windows": 0, "unknown_windows": 0,
                  "first_high_s": None, "first_joint_s": None} for z in zones}
    events = []
    previous_end = None
    for window in data["windows"]:
        duration = window["end_s"] - window["start_s"]
        if duration <= 0 or duration > 10 + TOL:
            raise ValueError("Invalid window duration")
        contiguous = previous_end is not None and abs(window["start_s"] - previous_end) < TOL
        for row in window["zones"]:
            z = row["zone_id"]
            if z not in totals:
                raise ValueError("Unknown region")
            valid = window["complete"] and window["held_duration_s"] == 0 and abs(duration - 10) < TOL
            if valid:
                totals[z]["eligible_windows"] += 1
            else:
                totals[z]["unknown_windows"] += 1
            high = valid and row["relative_count_level"] == "high"
            joint = high and row["motion_low_relative"] is True
            old_high, old_joint = streak[z] if contiguous else (0.0, 0.0)
            now_high = old_high + duration if high else 0.0
            now_joint = old_joint + duration if joint else 0.0
            streak[z] = [now_high, now_joint]
            if limit == 30:
                if abs(now_high - row["high_count_duration_s"]) > TOL or abs(now_joint - row["joint_duration_s"]) > TOL:
                    raise AssertionError(f"30 s streak mismatch: {camera}/{z}/{window['index']}")
                actual = row["state_hint"]
                if now_joint >= 30 - TOL:
                    expected = "high_count_low_motion_persistent"
                elif now_high >= 30 - TOL:
                    expected = "high_count_persistent"
                else:
                    expected = None
                if expected and actual != expected:
                    raise AssertionError(f"30 s hint mismatch: {camera}/{z}/{window['index']}")
                if not expected and actual in ("high_count_persistent", "high_count_low_motion_persistent"):
                    raise AssertionError(f"Spurious 30 s hint: {camera}/{z}/{window['index']}")
            h_alert = now_high >= limit - TOL
            j_alert = now_joint >= limit - TOL
            for kind, flag in (("high", h_alert), ("joint", j_alert)):
                if flag:
                    totals[z][kind + "_windows"] += 1
                    totals[z][kind + "_seconds"] += duration
                    if not prior[z][kind]:
                        totals[z][kind + "_episodes"] += 1
                        if totals[z]["first_" + kind + "_s"] is None:
                            totals[z]["first_" + kind + "_s"] = window["end_s"]
                        events.append({"camera": camera, "threshold_s": limit, "region": z,
                                       "type": kind, "confirmed_at_s": window["end_s"]})
                prior[z][kind] = flag
        previous_end = window["end_s"]
    return totals, events


def main():
    input_hashes = {}
    rows = []
    event_rows = []
    camera_totals = defaultdict(lambda: defaultdict(float))
    for camera in CAMERAS:
        path = BASE / camera / "states_10s.json"
        data, sha = read_json(path)
        input_hashes[camera] = {"path": str(path), "sha256": sha,
                                "source_id": data["source"]["id"]}
        for limit in LIMITS:
            totals, events = calculate(camera, data, limit)
            event_rows.extend(events)
            for region, result in totals.items():
                row = {"camera": camera, "threshold_s": limit, "region": region, **result}
                rows.append(row)
                for key in ("high_windows", "joint_windows", "high_seconds", "joint_seconds",
                            "high_episodes", "joint_episodes", "eligible_windows", "unknown_windows"):
                    camera_totals[(camera, limit)][key] += result[key]
    for camera in CAMERAS:
        subset = {(r["threshold_s"], r["region"]): r for r in rows if r["camera"] == camera}
        for region in {r["region"] for r in rows if r["camera"] == camera}:
            for kind in ("high", "joint"):
                counts = [subset[(t, region)][kind + "_windows"] for t in LIMITS]
                if counts != sorted(counts, reverse=True):
                    raise AssertionError("Longer threshold produced more alert windows")
    with (ROOT / "region_summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    with (ROOT / "first_alert_events.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["camera", "threshold_s", "region", "type", "confirmed_at_s"])
        writer.writeheader(); writer.writerows(event_rows)
    out = {"completed": True, "input_hashes": input_hashes,
           "method": "Only persistence duration varies. Count/motion calibration, 10 s windows, and evidence gates are fixed. Alerts are confirmed at window end. Held/incomplete windows reset streaks. Counts sum region-window alerts, not unique video minutes.",
           "thresholds_s": list(LIMITS), "reference_30s_matches_all_existing_streaks_and_alert_hints": True,
           "traffic_accuracy_evaluated": False,
           "camera_totals": [{"camera": c, "threshold_s": t, **dict(camera_totals[(c, t)])}
                             for c in CAMERAS for t in LIMITS],
           "region_summary": rows}
    (ROOT / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"completed": True, "rows": len(rows), "first_alert_events": len(event_rows),
                      "reference_30s_matches": True}))


if __name__ == "__main__":
    main()
