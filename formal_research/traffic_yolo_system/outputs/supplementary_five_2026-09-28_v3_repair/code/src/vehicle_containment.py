"""Optional, frozen containment filter for prototype heavy-vehicle counting.

This module only changes an explicitly supplied list of eligible raw indices.
It does not edit detector observations, infer vehicle identity, or use time,
track IDs, motion, or image coordinates specific to a particular incident.
An overlap candidate is evidence of geometric ambiguity, not a labelled
duplicate. In particular, overlapping real vehicles can satisfy this heuristic.
"""
from __future__ import annotations

import math
from numbers import Real


CLASS_NAMES = {0: "car", 1: "motorcycle", 2: "bus", 3: "truck"}
_FROZEN_THRESHOLDS = {
    "min_containment": 0.90,
    "min_area_ratio": 1.20,
    "max_bottom_gap_fraction": 0.15,
    "min_keeper_confidence": 0.35,
    "ambiguity_min_containment": 0.50,
}


def _finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def validate_policy(policy):
    """Validate the explicit, frozen development policy; never modify it.

    Descriptive metadata is allowed, but every behaviour-controlling field is
    mandatory and must match this bounded experiment's frozen settings.
    """
    if not isinstance(policy, dict):
        raise ValueError("Containment policy must be an object")
    if type(policy.get("schema_version")) is not int or policy["schema_version"] != 2:
        raise ValueError("Containment policy schema_version must be integer 2")
    if policy.get("representative_policy") != "confident_same_class_outer_then_low_confidence":
        raise ValueError("Containment policy must use the frozen two-stage representative policy")
    classes = policy.get("heavy_classes")
    if (not isinstance(classes, list) or any(type(item) is not int for item in classes)
            or classes != [2, 3]):
        raise ValueError("Containment policy heavy_classes must be [2, 3] (bus, truck)")
    if policy.get("development_only") is not True:
        raise ValueError("Containment policy must explicitly set development_only=true")
    for key, expected in _FROZEN_THRESHOLDS.items():
        value = _finite_number(policy.get(key), f"policy.{key}")
        if value != expected:
            raise ValueError(f"Frozen containment policy requires {key}={expected}")


def _validate_detections(raw, eligible):
    if not isinstance(raw, list):
        raise ValueError("raw detections must be a list")
    for index, detection in enumerate(raw):
        label = f"raw[{index}]"
        if not isinstance(detection, dict):
            raise ValueError(f"{label} must be an object")
        class_id = detection.get("class_id")
        if type(class_id) is not int or class_id not in CLASS_NAMES:
            raise ValueError(f"{label}.class_id does not match the frozen class map")
        if detection.get("class_name") != CLASS_NAMES[class_id]:
            raise ValueError(f"{label}.class_name does not match class_id")
        confidence = _finite_number(detection.get("confidence"), f"{label}.confidence")
        if not 0 <= confidence <= 1:
            raise ValueError(f"{label}.confidence must lie in [0, 1]")
        box = detection.get("box_xyxy")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            raise ValueError(f"{label}.box_xyxy must have four coordinates")
        x1, y1, x2, y2 = [_finite_number(value, f"{label}.box_xyxy") for value in box]
        if min(x1, y1) < 0 or x2 <= x1 or y2 <= y1:
            raise ValueError(f"{label}.box_xyxy requires nonnegative coordinates and positive area")
        area = (x2 - x1) * (y2 - y1)
        if not math.isfinite(area) or area <= 0:
            raise ValueError(f"{label}.box_xyxy requires a finite positive area")
    if not isinstance(eligible, list):
        raise ValueError("eligible indices must be a list")
    if any(type(index) is not int or not 0 <= index < len(raw) for index in eligible):
        raise ValueError("eligible indices must be integer raw detection indices")
    if eligible != sorted(set(eligible)):
        raise ValueError("eligible indices must be unique and sorted in original order")


def _geometry(a, b):
    ax1, ay1, ax2, ay2 = (float(value) for value in a["box_xyxy"])
    bx1, by1, bx2, by2 = (float(value) for value in b["box_xyxy"])
    height_a, height_b = ay2 - ay1, by2 - by1
    area_a, area_b = (ax2 - ax1) * height_a, (bx2 - bx1) * height_b
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0.0, min(ay2, by2) - max(ay1, by1))
    min_area, max_area = min(area_a, area_b), max(area_a, area_b)
    min_height = min(height_a, height_b)
    gap = abs(ay2 - by2)
    geometry = {
        "intersection_area": intersection,
        "min_area": min_area,
        "max_area": max_area,
        "containment": intersection / min_area,
        "area_ratio": max_area / min_area,
        "bottom_gap": gap,
        "min_height": min_height,
        "bottom_gap_fraction": gap / min_height,
    }
    if any(not math.isfinite(value) for value in geometry.values()):
        raise ValueError("Containment pair geometry overflowed the finite numeric range")
    return geometry


def _failed_conditions(geometry, keeper, policy):
    failed = []
    if geometry["containment"] < policy["min_containment"]:
        failed.append("containment_below_minimum")
    if geometry["area_ratio"] < policy["min_area_ratio"]:
        failed.append("area_ratio_below_minimum")
    if geometry["bottom_gap_fraction"] > policy["max_bottom_gap_fraction"]:
        failed.append("bottom_gap_above_maximum")
    if keeper["confidence"] < policy["min_keeper_confidence"]:
        failed.append("keeper_confidence_below_minimum")
    return failed


def _pair_details(raw, removed_index, kept_index, geometry):
    return {
        "removed_index": removed_index,
        "kept_index": kept_index,
        **geometry,
        "classes": {
            "removed": raw[removed_index]["class_name"],
            "kept": raw[kept_index]["class_name"],
        },
        "class_ids": {
            "removed": raw[removed_index]["class_id"],
            "kept": raw[kept_index]["class_id"],
        },
        "confidences": {
            "removed": raw[removed_index]["confidence"],
            "kept": raw[kept_index]["confidence"],
        },
    }


def suppress_contained_vehicles(raw, eligible, policy):
    """Return ``(kept_indices, removed, unresolved_pairs)`` without mutation.

    Stage A visits confident (score >= 0.35) heavy-vehicle detections by
    descending area, then descending score, then original index. Only a
    previously retained, larger, same-class confident box can suppress a
    smaller confident box. This avoids selecting a confident bus fragment
    instead of an also-confident whole-bus box. Confident different-class
    pairs are not merged by this filter.

    Stage B considers remaining low-confidence heavy-vehicle candidates
    (score < 0.35). A candidate can be suppressed only by a Stage A survivor,
    considered by descending score then original index. This stage permits
    either heavy class and either area direction, but never lets a low-score
    box suppress another observation. All geometric thresholds are inclusive.

    Both the keeper and the removed candidate refer to original raw indices;
    kept_indices is sorted back into original order for tracker replay. A
    keeper can be the larger or smaller box in Stage B. The unresolved list
    includes only pairs of final retained heavy-vehicle detections with IoS
    >= 0.50 that fail one or more suppression conditions. These are geometric
    candidates for automated diagnostics, never assertions of true duplicates.
    """
    validate_policy(policy)
    _validate_detections(raw, eligible)
    heavy = set(policy["heavy_classes"])
    high_threshold = policy["min_keeper_confidence"]
    high = [index for index in eligible
            if raw[index]["class_id"] in heavy and raw[index]["confidence"] >= high_threshold]
    low = [index for index in eligible
           if raw[index]["class_id"] in heavy and raw[index]["confidence"] < high_threshold]

    def area(index):
        x1, y1, x2, y2 = (float(value) for value in raw[index]["box_xyxy"])
        return (x2 - x1) * (y2 - y1)

    retained_high, removed = [], []
    for index in sorted(high, key=lambda item: (-area(item), -raw[item]["confidence"], item)):
        candidate = raw[index]
        suppressor = None
        for keeper_index in retained_high:
            keeper = raw[keeper_index]
            if keeper["class_id"] != candidate["class_id"]:
                continue
            geometry = _geometry(candidate, keeper)
            if not _failed_conditions(geometry, keeper, policy):
                suppressor = {
                    **_pair_details(raw, index, keeper_index, geometry),
                    "stage": "A",
                    "reason": "confident_same_class_outer_box_representative",
                }
                break
        if suppressor is None:
            retained_high.append(index)
        else:
            removed.append(suppressor)

    kept = [index for index in eligible if raw[index]["class_id"] not in heavy] + retained_high
    high_by_score = sorted(retained_high, key=lambda item: (-raw[item]["confidence"], item))
    for index in sorted(low, key=lambda item: (-raw[item]["confidence"], item)):
        suppressor = None
        for keeper_index in high_by_score:
            geometry = _geometry(raw[index], raw[keeper_index])
            if not _failed_conditions(geometry, raw[keeper_index], policy):
                suppressor = {
                    **_pair_details(raw, index, keeper_index, geometry),
                    "stage": "B",
                    "reason": "low_confidence_heavy_box_contained_by_confident_survivor",
                }
                break
        if suppressor is None:
            kept.append(index)
        else:
            removed.append(suppressor)

    unresolved_pairs = []
    retained_heavy = sorted(index for index in kept if raw[index]["class_id"] in heavy)
    for rank, first_index in enumerate(retained_heavy):
        for second_index in retained_heavy[rank + 1:]:
            first, second = raw[first_index], raw[second_index]
            both_high = min(first["confidence"], second["confidence"]) >= high_threshold
            same_class = first["class_id"] == second["class_id"]
            if both_high and same_class:
                keeper_index, candidate_index = sorted(
                    [first_index, second_index],
                    key=lambda item: (-area(item), -raw[item]["confidence"], item))
            else:
                keeper_index, candidate_index = sorted(
                    [first_index, second_index], key=lambda item: (-raw[item]["confidence"], item))
            geometry = _geometry(raw[candidate_index], raw[keeper_index])
            if geometry["containment"] < policy["ambiguity_min_containment"]:
                continue
            failed = _failed_conditions(geometry, raw[keeper_index], policy)
            if both_high and not same_class:
                failed.append("confident_different_class_pair_not_merged")
            unresolved_pairs.append({
                "indices": sorted([keeper_index, candidate_index]),
                "preferred_keeper_index": keeper_index,
                "candidate_index": candidate_index,
                **geometry,
                "classes": {str(index): raw[index]["class_name"]
                            for index in sorted([keeper_index, candidate_index])},
                "confidences": {str(index): raw[index]["confidence"]
                                for index in sorted([keeper_index, candidate_index])},
                "failed_conditions": failed,
                "reason": "unresolved_geometric_overlap_not_confirmed_duplicate",
            })
    return sorted(kept), removed, unresolved_pairs
