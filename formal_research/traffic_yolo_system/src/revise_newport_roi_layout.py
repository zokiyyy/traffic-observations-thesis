"""Create Newport's second ROI layout; keep all saved detector/track data intact.

Only the central polygon expands to meet the three unchanged road polygons.
These are image-space counting areas, not calibrated lanes or congestion labels.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


SHARED_BORDERS = {
    "road_left": [[375, 214], [335, 266], [270, 358], [203, 463], [177, 482]],
    "road_top": [[533, 139], [592, 126], [673, 110], [748, 94]],
    "road_right": [[783, 108], [842, 137], [871, 168], [903, 205]],
}
NEW_CENTER = [
    [375, 214], [407, 207], [528, 150], [533, 139], [592, 126],
    [673, 110], [748, 94], [783, 108], [842, 137], [871, 168],
    [903, 205], [882, 263], [878, 273], [240, 526], [220, 498],
    [177, 482], [203, 463], [270, 358], [335, 266],
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    original = json.loads(args.source.read_text())
    source_hash = hashlib.sha256(args.source.read_bytes()).hexdigest()
    # This revision is intentionally specific to the reviewed Newport camera.
    expected = "2de2e9e7b37846683499a3433d4fb3226ec5705c17c14d3c1726fef2e78b33f4"
    if source_hash != expected:
        raise ValueError("This geometry revision requires the reviewed Newport v1 layout.")
    layout = copy.deepcopy(original)
    layout.update(
        schema_version=2,
        layout_version="newport_v2_shared_internal_boundaries_2026-09-24",
        boundary_tie_break="zone_order",
        assignment_policy=(
            "Use the tracked box bottom-center. Polygon interiors must be disjoint. "
            "A point exactly on a shared edge/vertex belongs to the first zone in "
            "the zones list (road approaches before junction_center), counted once. "
            "Points outside the selected road surface remain outside_roi."
        ),
        status="provisional_shared_boundary_revision",
        review_basis=[
            "AI visual review of original source frames, road edges and existing internal gaps.",
            "Keep road_left, road_top and road_right geometry unchanged; extend only junction_center.",
            "Share the full junction-facing polylines; do not fit polygon coordinates to one vehicle.",
            "Keep outer road margins, traffic islands, footpaths and the foreground-obscured approach excluded.",
        ],
    )
    layout["limitations"] = [
        item for item in original["limitations"] if "border band" not in item
    ] + [
        "Internal shared borders replace the old excluded bands; counts cover a slightly larger visible road surface.",
        "Count increases from this layout revision are changes of counting coverage, not evidence of detector accuracy improvement.",
        "Outer-border jitter, false vehicle detections and untracked distant detections remain separate issues.",
    ]
    assert [z["zone_name"] for z in layout["zones"]] == [
        "road_left", "road_top", "road_right", "junction_center"
    ]
    center = layout["zones"][-1]
    center["polygon_points"] = NEW_CENTER
    center["description"] = (
        "Visible central junction surface, extended to the unchanged approach boundaries. "
        "Shared borders have one counting owner. Not a queue or calibrated lane."
    )
    layout["revision"] = {
        "previous_layout": str(args.source.resolve()),
        "previous_layout_sha256": source_hash,
        "changed_zone": "junction_center",
        "unchanged_zones": list(SHARED_BORDERS),
        "shared_internal_polylines": SHARED_BORDERS,
        "old_center_polygon_points": original["zones"][-1]["polygon_points"],
        "detector_or_tracker_changes": False,
        "human_ground_truth_created": False,
    }
    # Do not retain v1's pixel-overlap claim: shared edge pixels now coincide.
    layout.pop("geometry_checks", None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as out:
        json.dump(layout, out, ensure_ascii=False, indent=2)
        out.write("\n")
    assert hashlib.sha256(args.source.read_bytes()).hexdigest() == source_hash
    print(json.dumps({"output": str(args.output), "changed_zone": "junction_center"}))


if __name__ == "__main__":
    main()
