"""Validated road ROIs and one shared bottom-centre assignment implementation.

Legacy layouts omit ``boundary_tie_break`` and retain the previous ``error``
policy, including its conservative pixel-coverage check. Explicit ``zone_order``
layouts allow shared edges/vertices: a point on multiple polygon boundaries goes
to the first zone in layout order. This does not permit overlapping interiors.

Geometry validation uses float64 simple-polygon checks, ear triangulation and
convex clipping. It is independent of raster resolution and detects subpixel
overlap. Area differences at or below 1e-8 square pixels are numerical noise.
No polygon expansion, nearest-zone fallback or lost-track extrapolation occurs.
"""
from __future__ import annotations

from itertools import combinations

import cv2
import numpy as np


GEOMETRY_EPSILON = 1e-9
OVERLAP_AREA_EPSILON = 1e-8


def boundary_policy(layout):
    policy = layout.get("boundary_tie_break", "error")
    if policy not in ("error", "zone_order"):
        raise ValueError(f"Unsupported boundary_tie_break: {policy!r}")
    return policy


def assignment_metadata(layout):
    policy = boundary_policy(layout)
    return {
        "assignment_point": "bottom_center",
        "boundary_tie_break": policy,
        "zone_order": [zone["zone_name"] for zone in layout["zones"]],
        "shared_boundary_rule": (
            "First zone in zone_order wins only when all hits are on boundaries."
            if policy == "zone_order" else "Multiple inclusive polygon hits are an error."
        ),
        "interior_overlap": "rejected by float64 simple-polygon triangulation and intersection-area checks",
        "overlap_area_tolerance_square_pixels": OVERLAP_AREA_EPSILON,
        "point_test_precision": "float64" if policy == "zone_order" else "legacy OpenCV float32",
        "outside_rule": "A point not in or on any polygon remains outside_roi; no nearest-zone reassignment.",
    }


def _cross(a, b, c):
    return float((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))


def _signed_area(poly):
    # Shift the origin to reduce cancellation for small polygons far from (0,0).
    shifted = np.asarray(poly, dtype=np.float64) - poly[0]
    return float(sum(_cross(shifted[0], shifted[i], shifted[i + 1])
                     for i in range(1, len(shifted) - 1)) / 2)


def _on_segment(a, b, point):
    return (abs(_cross(a, b, point)) <= GEOMETRY_EPSILON
            and min(a[0], b[0]) - GEOMETRY_EPSILON <= point[0] <= max(a[0], b[0]) + GEOMETRY_EPSILON
            and min(a[1], b[1]) - GEOMETRY_EPSILON <= point[1] <= max(a[1], b[1]) + GEOMETRY_EPSILON)


def _segments_touch_or_cross(a, b, c, d):
    ab_c, ab_d, cd_a, cd_b = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
    if ((ab_c > GEOMETRY_EPSILON and ab_d < -GEOMETRY_EPSILON)
            or (ab_c < -GEOMETRY_EPSILON and ab_d > GEOMETRY_EPSILON)) and (
            (cd_a > GEOMETRY_EPSILON and cd_b < -GEOMETRY_EPSILON)
            or (cd_a < -GEOMETRY_EPSILON and cd_b > GEOMETRY_EPSILON)):
        return True
    return any((_on_segment(a, b, c), _on_segment(a, b, d),
                _on_segment(c, d, a), _on_segment(c, d, b)))


def _simple_polygon(points, name):
    poly = np.asarray(points, dtype=np.float64)
    if poly.ndim != 2 or poly.shape[1] != 2 or len(poly) < 3 or not np.isfinite(poly).all():
        raise ValueError(f"Invalid polygon for {name}")
    if np.array_equal(poly[0], poly[-1]):
        poly = poly[:-1]
    if len(poly) < 3 or len({tuple(p) for p in poly}) != len(poly):
        raise ValueError(f"Repeated vertices or degenerate polygon for {name}")
    n = len(poly)
    for i in range(n):
        prev, point, following = poly[(i - 1) % n], poly[i], poly[(i + 1) % n]
        if abs(_cross(prev, point, following)) <= GEOMETRY_EPSILON and np.dot(point - prev, following - point) < 0:
            raise ValueError(f"Self-overlapping adjacent edges for {name}")
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            if _segments_touch_or_cross(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n]):
                raise ValueError(f"Self-intersecting polygon for {name}")
    if abs(_signed_area(poly)) <= OVERLAP_AREA_EPSILON:
        raise ValueError(f"Degenerate polygon for {name}")
    return poly


def _triangulate(polygon):
    vertices = [point.copy() for point in polygon]
    # Forward collinear vertices add no area and can prevent finding an ear.
    changed = True
    while changed and len(vertices) > 3:
        changed = False
        for i in range(len(vertices)):
            if abs(_cross(vertices[i - 1], vertices[i], vertices[(i + 1) % len(vertices)])) <= GEOMETRY_EPSILON:
                del vertices[i]
                changed = True
                break
    if _signed_area(vertices) < 0:
        vertices.reverse()
    triangles = []
    while len(vertices) > 3:
        for i in range(len(vertices)):
            a, b, c = vertices[i - 1], vertices[i], vertices[(i + 1) % len(vertices)]
            if _cross(a, b, c) <= GEOMETRY_EPSILON:
                continue
            other = [p for j, p in enumerate(vertices) if j not in ((i - 1) % len(vertices), i, (i + 1) % len(vertices))]
            if any(min(_cross(a, b, p), _cross(b, c, p), _cross(c, a, p)) >= -GEOMETRY_EPSILON for p in other):
                continue
            triangles.append(np.asarray([a, b, c]))
            del vertices[i]
            break
        else:
            raise ValueError("Could not triangulate a polygon; check its geometry.")
    triangles.append(np.asarray(vertices))
    return triangles


def _triangle_intersection_area(subject, clip):
    # Both triangles are counter-clockwise. Sutherland-Hodgman in float64.
    output = list(subject)
    for i in range(3):
        a, b = clip[i], clip[(i + 1) % 3]
        incoming, output = output, []
        if not incoming:
            return 0.0
        previous = incoming[-1]
        previous_distance = _cross(a, b, previous)
        for current in incoming:
            current_distance = _cross(a, b, current)
            previous_inside = previous_distance >= -GEOMETRY_EPSILON
            current_inside = current_distance >= -GEOMETRY_EPSILON
            if current_inside != previous_inside:
                divisor = previous_distance - current_distance
                if abs(divisor) > GEOMETRY_EPSILON:
                    output.append(previous + (current - previous) * (previous_distance / divisor))
            if current_inside:
                output.append(current)
            previous, previous_distance = current, current_distance
    return abs(_signed_area(output)) if len(output) >= 3 else 0.0


def validate_layout(layout):
    """Return ordered ``(name, float64 polygon)`` pairs after strict validation."""
    if layout.get("assignment_point") != "bottom_center":
        raise ValueError("Only bottom_center zone assignment is supported.")
    policy = boundary_policy(layout)
    width, height = int(layout["frame_width"]), int(layout["frame_height"])
    if width <= 0 or height <= 0 or not layout.get("zones"):
        raise ValueError("Positive frame dimensions and at least one zone are required.")
    polygons, names = [], set()
    for zone in layout["zones"]:
        name = zone["zone_name"]
        if not isinstance(name, str) or not name or name == "outside_roi" or name in names:
            raise ValueError(f"Duplicate, empty or reserved zone name: {name!r}")
        names.add(name)
        poly = _simple_polygon(zone["polygon_points"], name)
        if not ((poly[:, 0] >= 0).all() and (poly[:, 0] < width).all()
                and (poly[:, 1] >= 0).all() and (poly[:, 1] < height).all()):
            raise ValueError(f"Polygon {name} is outside the frame.")
        polygons.append((name, poly))
    triangles = {name: _triangulate(poly) for name, poly in polygons}
    for (name_a, _), (name_b, _) in combinations(polygons, 2):
        intersection_area = sum(_triangle_intersection_area(a, b)
                                for a in triangles[name_a] for b in triangles[name_b])
        if intersection_area > OVERLAP_AREA_EPSILON:
            raise ValueError(f"Zone interiors overlap: {name_a}, {name_b}; area={intersection_area:.12g} square pixels")
    if policy == "error":
        # Preserve the old conservative raster check unless shared boundaries
        # are explicitly opted into. No rounding is used for point assignment.
        occupied = np.zeros((height, width), dtype=np.uint8)
        for name, poly in polygons:
            mask = np.zeros_like(occupied)
            cv2.fillPoly(mask, [poly.astype(np.int32)], 1)
            if np.any(occupied & mask):
                raise ValueError(f"Zones overlap at pixel scale near {name}; boundary_tie_break='zone_order' is required for shared boundaries.")
            occupied |= mask
    return polygons


def _float64_point_position(point, polygon):
    """Return -1 outside, 0 on boundary, 1 inside, without pixel quantization."""
    inside = False
    x, y = point
    for i, a in enumerate(polygon):
        b = polygon[(i + 1) % len(polygon)]
        if _on_segment(a, b, point):
            return 0
        if (a[1] > y) != (b[1] > y):
            crossing_x = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x < crossing_x:
                inside = not inside
    return 1 if inside else -1


def assign_point(point, polygons, boundary_tie_break="error"):
    """Assign one point uniquely using *validated*, ordered polygon pairs.

    Legacy mode retains OpenCV float32 point tests for identical old counts.
    The explicit shared-boundary mode uses float64 to avoid merging narrow gaps
    or shifting shared edges through float32 quantization.
    """
    if boundary_tie_break not in ("error", "zone_order"):
        raise ValueError(f"Unsupported boundary_tie_break: {boundary_tie_break!r}")
    point = tuple(float(v) for v in point)
    if len(point) != 2 or not np.isfinite(point).all():
        raise ValueError(f"Invalid assignment point: {point}")
    hits = []
    for name, polygon in polygons:
        position = (cv2.pointPolygonTest(np.asarray(polygon, dtype=np.float32), point, False)
                    if boundary_tie_break == "error" else _float64_point_position(point, polygon))
        if position >= 0:
            hits.append((name, position))
    if len(hits) > 1 and (boundary_tie_break == "error" or any(position > 0 for _, position in hits)):
        raise ValueError(f"Overlapping zone assignment at {point}: {[name for name, _ in hits]}")
    return hits[0][0] if hits else "outside_roi"


def assign_box(box, polygons, boundary_tie_break="error"):
    if len(box) != 4 or not np.isfinite(box).all():
        raise ValueError(f"Invalid box: {box}")
    return assign_point(((float(box[0]) + float(box[2])) / 2, float(box[3])),
                        polygons, boundary_tie_break)
