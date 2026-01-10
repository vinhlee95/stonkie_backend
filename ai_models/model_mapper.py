import logging
from typing import Dict

from ai_models.model_name import ModelName

logger = logging.getLogger(__name__)

# Frontend model name mapping (case-insensitive, normalized)
# Maps user-friendly model names from frontend to internal ModelName enum values
FRONTEND_MODEL_MAP: Dict[str, ModelName] = {
    "fastest": ModelName.Fastest,  # "fastest" maps to Gemini 2.5 Flash Lite with :nitro variant for high-speed inference
    "best": ModelName.Auto,  # "best" also maps to Auto Router (backward compatibility)
    "auto": ModelName.Auto,
    "gemini-3.0-flash": ModelName.Gemini30Flash,
    "gemini-3-flash-preview": ModelName.Gemini30Flash,
    "gemini-2.5-flash": ModelName.Gemini25Flash,
    "gemini-2.5-flash-lite": ModelName.Gemini25FlashLite,
}


def map_frontend_model_to_enum(frontend_model: str) -> ModelName:
    """
    Map frontend model name to ModelName enum with normalization.
    Handles case-insensitive, space/dash variations.

    Args:
        frontend_model: Model name from frontend (e.g., "Gemini 3.0 Flash", "fastest")

    Returns:
        ModelName enum value, defaults to Auto if invalid
    """
    # Normalize: lowercase, replace spaces with dashes
    normalized = frontend_model.lower().strip().replace(" ", "-").replace("_", "-")

    # Try direct mapping
    model = FRONTEND_MODEL_MAP.get(normalized)
    if model:
        return model

    # Log warning for unknown models and default to Auto Router
    logger.warning(f"Unknown model requested: '{frontend_model}', defaulting to Auto Router")
    return ModelName.Auto
