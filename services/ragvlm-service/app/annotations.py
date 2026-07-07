from __future__ import annotations

from typing import Any

OPERATOR_COORD_MAX = 100.0
RAGVLM_COORD_MAX = 1000.0
COORD_SCALE = RAGVLM_COORD_MAX / OPERATOR_COORD_MAX

COORDINATE_KEYS = {
    "x",
    "y",
    "width",
    "height",
    "cx",
    "cy",
    "r",
    "radius",
    "left",
    "top",
    "right",
    "bottom",
}


def _scale_coordinate(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    return max(0.0, min(RAGVLM_COORD_MAX, float(value) * COORD_SCALE))


def _scale_point(point: Any) -> Any:
    if not isinstance(point, dict):
        return point
    scaled = dict(point)
    if "x" in scaled:
        scaled["x"] = _scale_coordinate(scaled["x"])
    if "y" in scaled:
        scaled["y"] = _scale_coordinate(scaled["y"])
    return scaled


def normalize_annotation(annotation: dict[str, Any]) -> dict[str, Any]:
    """Convert OperatorOS 0-100 SVG annotation coordinates to RAGVLM 0-1000 space."""
    normalized: dict[str, Any] = {}
    for key, value in annotation.items():
        if key in COORDINATE_KEYS:
            normalized[key] = _scale_coordinate(value)
        elif key in {"points", "path"} and isinstance(value, list):
            normalized[key] = [_scale_point(point) for point in value]
        elif key in {"bbox", "box"} and isinstance(value, list):
            normalized[key] = [_scale_coordinate(item) for item in value]
        else:
            normalized[key] = value
    normalized["coordinate_space"] = "ragvlm_0_1000"
    return normalized


def normalize_annotations(annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_annotation(annotation) for annotation in annotations]
