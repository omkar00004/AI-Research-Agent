import os
from langchain_groq import ChatGroq


def get_llm(model=None, temperature=0.3, role=None):
    """Get a ChatGroq instance with optional role-based model routing.

    Args:
        model: Explicit model name override. Takes priority over ``role``.
        temperature: Sampling temperature for generation.
        role: Agent role key (e.g. ``"planner"``, ``"researcher"``).
              When provided and ``model`` is *None*, the model is looked
              up from ``config.MODEL_CONFIG[role]``.

    Falls back to ``llama-3.3-70b-versatile`` if neither ``model`` nor
    ``role`` resolves to a valid model name.

    Reads a comma-separated list of Groq API keys from ``GROQ_API_KEY``
    and sets up automatic fallback rotation when rate-limited.
    """
    # Resolve model name: explicit > role-config > default
    if model is None and role is not None:
        try:
            from config import MODEL_CONFIG
            model = MODEL_CONFIG.get(role, "llama-3.3-70b-versatile")
        except ImportError:
            model = "llama-3.3-70b-versatile"
    elif model is None:
        model = "llama-3.3-70b-versatile"

    keys_str = os.getenv("GROQ_API_KEY", "")
    # Split and strip keys
    keys = [k.strip() for k in keys_str.split(",") if k.strip()]

    if not keys:
        # Will fail gracefully downstream if no key is provided
        return ChatGroq(model=model, api_key="", temperature=temperature)

    primary_llm = ChatGroq(
        model=model,
        api_key=keys[0],
        temperature=temperature,
    )

    # If multiple keys are provided, set up fallbacks for rate limits
    if len(keys) > 1:
        fallbacks = [
            ChatGroq(model=model, api_key=k, temperature=temperature)
            for k in keys[1:]
        ]
        return primary_llm.with_fallbacks(fallbacks)

    return primary_llm
