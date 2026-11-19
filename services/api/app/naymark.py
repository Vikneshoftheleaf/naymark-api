from __future__ import annotations

import numpy as np

try:
    from naymark import remove_watermark
except ImportError as exc:
    raise RuntimeError("naymark==0.1.0 is required") from exc


def remove_supported_watermark(image: np.ndarray) -> np.ndarray:
    """Apply the library defaults for supported visible Gemini watermarks."""
    result = remove_watermark(image, threshold=0.6, max_watermarks=5)
    cleaned = getattr(result, "image", result)
    if not isinstance(cleaned, np.ndarray):
        cleaned = np.asarray(cleaned)
    return cleaned
