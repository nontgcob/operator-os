from __future__ import annotations

import sys
from pathlib import Path

RAGVLM_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "ragvlm-service" / "app"
sys.path.append(str(RAGVLM_APP_PATH))

from annotations import normalize_annotations  # type: ignore  # noqa: E402
from prompts import build_prompt  # type: ignore  # noqa: E402


def test_annotation_normalization_scales_svg_coordinates() -> None:
    normalized = normalize_annotations(
        [
            {
                "type": "rect",
                "color": "#DA5854",
                "x": 12.5,
                "y": 20,
                "width": 8,
                "height": 4,
                "points": [{"x": 1, "y": 2}],
            }
        ]
    )

    annotation = normalized[0]
    assert annotation["coordinate_space"] == "ragvlm_0_1000"
    assert annotation["x"] == 125.0
    assert annotation["y"] == 200.0
    assert annotation["width"] == 80.0
    assert annotation["height"] == 40.0
    assert annotation["points"] == [{"x": 10.0, "y": 20.0}]


def test_prompt_includes_ragvlm_grounding_sections() -> None:
    prompt = build_prompt(
        "What valve should the operator check?",
        [{"type": "rect", "x": 100.0, "y": 200.0, "coordinate_space": "ragvlm_0_1000"}],
        "[1.00-2.00] Operator points at the lower valve.",
        "Manual: close the lower valve before calibration.",
        model_family="gemini",
        video_title="Pump Room Safety Walkthrough",
    )

    assert "RAGVLM 0-1000 image coordinates" in prompt
    assert "Model family: gemini" in prompt
    assert "Video title:\nPump Room Safety Walkthrough" in prompt
    assert "Normalized annotations:" in prompt
    assert "## Retrieved context" in prompt
    assert "lower valve before calibration" in prompt


def test_prompt_includes_tight_annotation_quality_rules() -> None:
    prompt = build_prompt(
        "Draw boxes around the words CELSIUS and FIZZ-FREE and around both thumbnail nails.",
        [],
        "No transcript.",
        "No retrieved document excerpts.",
        model_family="qwen",
        video_title="Can close-up inspection",
    )

    assert "ANNOTATION QUALITY RULES:" in prompt
    assert "For printed words, labels, logos, or short text spans, prefer tight `rect` boxes" in prompt
    assert "For very small objects or sub-parts such as fingernails" in prompt
    assert "Keep annotation geometry consistent across similar targets in the same image" in prompt
    assert "For thumbnails or other tiny parts, annotate only the nail itself when visible" in prompt
    assert "Return one annotation per requested target when the user names distinct targets" in prompt
