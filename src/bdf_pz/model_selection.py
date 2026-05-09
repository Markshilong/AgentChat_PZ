from __future__ import annotations

from typing import Any


def get_query_available_models() -> list[Any]:
    """
    Return a non-empty list of Palimpzest models that are usable in the current env.

    We prefer introspecting Palimpzest's own model helper so the choice tracks
    whichever provider keys are actually configured in the running kernel.
    """
    try:
        from palimpzest.utils.model_helpers import get_models

        models = list(get_models(include_embedding=False))
        if models:
            return models[:5]
    except Exception:
        pass

    from palimpzest.constants import Model

    fallbacks = []
    for candidate_name in [
        "GPT_4o",
        "GPT_4_1",
        "GPT_4_1_MINI",
        "GPT_5",
        "CLAUDE_4_5_SONNET",
    ]:
        candidate = getattr(Model, candidate_name, None)
        if candidate is not None:
            fallbacks.append(candidate)

    if not fallbacks:
        raise RuntimeError("No Palimpzest models are available in the current environment.")
    return fallbacks
