from __future__ import annotations

DEFAULT_MODEL = "qwen/qwen3-vl-8b-instruct"

MODEL_FAMILIES = {
    "gemini": {
        "models": {
            "google/gemini-3.1-pro-preview",
            "google/gemini-3-flash-preview",
        },
        "text_model": "google/gemini-3-flash-preview",
        "image_model": "google/gemini-3.1-pro-preview",
        "image_fast_model": "google/gemini-3-flash-preview",
        "supports_images": True,
        "supports_documents": True,
    },
    "gpt": {
        "models": {
            "openai/gpt-5-chat",
            "openai/gpt-5-mini",
        },
        "text_model": "openai/gpt-5-mini",
        "image_model": "openai/gpt-5-chat",
        "image_fast_model": "openai/gpt-5-mini",
        "supports_images": True,
        "supports_documents": True,
    },
    "qwen": {
        "models": {
            "qwen/qwen3-vl-235b-a22b-instruct",
            "qwen/qwen3-vl-8b-instruct",
        },
        "text_model": "qwen/qwen3-vl-8b-instruct",
        "image_model": "qwen/qwen3-vl-235b-a22b-instruct",
        "image_fast_model": "qwen/qwen3-vl-8b-instruct",
        "supports_images": True,
        "supports_documents": True,
    },
}


def model_family_for(model: str) -> str:
    for family, config in MODEL_FAMILIES.items():
        if model in config["models"]:
            return family
    return "custom"


def model_supports_reasoning(model: str) -> bool:
    return model == "google/gemini-3.1-pro-preview"
