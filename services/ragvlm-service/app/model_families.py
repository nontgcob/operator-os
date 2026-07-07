from __future__ import annotations

DEFAULT_MODEL = "google/gemini-3.1-pro-preview"

MODEL_FAMILIES = {
    "gemini": {
        "models": {
            DEFAULT_MODEL,
            "google/gemini-2.5-pro",
            "google/gemini-2.5-flash",
        },
        "supports_images": True,
        "supports_documents": True,
    },
    "openai": {
        "models": {
            "openai/gpt-4.1",
            "openai/gpt-4.1-mini",
        },
        "supports_images": True,
        "supports_documents": True,
    },
    "anthropic": {
        "models": {
            "anthropic/claude-sonnet-4",
            "anthropic/claude-3.7-sonnet",
        },
        "supports_images": True,
        "supports_documents": True,
    },
}


def model_family_for(model: str) -> str:
    for family, config in MODEL_FAMILIES.items():
        if model in config["models"]:
            return family
    return "custom"
