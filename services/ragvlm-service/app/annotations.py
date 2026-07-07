from __future__ import annotations

from typing import Any

OPERATOR_COORD_MAX = 100.0
RAGVLM_COORD_MAX = 1000.0
COORD_SCALE = RAGVLM_COORD_MAX / OPERATOR_COORD_MAX

COORDINATE_KEYS = {
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
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


def _clamp_coordinate(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    return max(0.0, min(RAGVLM_COORD_MAX, float(value)))


def _scale_coordinate(value: Any, *, already_ragvlm: bool) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    if already_ragvlm:
        return _clamp_coordinate(value)
    return _clamp_coordinate(float(value) * COORD_SCALE)


def _scale_point(point: Any, *, already_ragvlm: bool) -> Any:
    if isinstance(point, list):
        return [_scale_coordinate(item, already_ragvlm=already_ragvlm) for item in point]
    if not isinstance(point, dict):
        return point
    scaled = dict(point)
    if "x" in scaled:
        scaled["x"] = _scale_coordinate(scaled["x"], already_ragvlm=already_ragvlm)
    if "y" in scaled:
        scaled["y"] = _scale_coordinate(scaled["y"], already_ragvlm=already_ragvlm)
    return scaled


def normalize_annotation(annotation: dict[str, Any]) -> dict[str, Any]:
    """Convert OperatorOS 0-100 SVG annotation coordinates to RAGVLM 0-1000 space."""
    already_ragvlm = annotation.get("coordinate_space") == "ragvlm_0_1000"
    normalized: dict[str, Any] = {}
    for key, value in annotation.items():
        if key in COORDINATE_KEYS:
            normalized[key] = _scale_coordinate(value, already_ragvlm=already_ragvlm)
        elif key in {"points", "path"} and isinstance(value, list):
            normalized[key] = [_scale_point(point, already_ragvlm=already_ragvlm) for point in value]
        elif key in {"bbox", "box"} and isinstance(value, list):
            normalized[key] = [_scale_coordinate(item, already_ragvlm=already_ragvlm) for item in value]
        else:
            normalized[key] = value
    normalized["coordinate_space"] = "ragvlm_0_1000"
    return normalized


def normalize_annotations(annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_annotation(annotation) for annotation in annotations]
