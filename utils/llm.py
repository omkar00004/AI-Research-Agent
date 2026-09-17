import os
from langchain_groq import ChatGroq


_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _get_openrouter_llm(model: str, temperature: float):
    """Return a ChatOpenAI instance wired to OpenRouter.

    OpenRouter exposes an OpenAI-compatible REST API, so ``langchain-openai``
    works without modification — we just swap the ``base_url`` and ``api_key``.

    Args:
        model: Full OpenRouter model slug, e.g.
               ``"meta-llama/llama-3.3-70b-instruct"``.
        temperature: Sampling temperature.
    """
    from langchain_openai import ChatOpenAI  # lazy import — only when needed

    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=_OPENROUTER_BASE_URL,
        temperature=temperature,
        default_headers={
            # Optional but recommended by OpenRouter for analytics
            "HTTP-Referer": "https://atlas-research-agent",
            "X-Title": "Atlas Research Agent",
        },
    )


def get_llm(model=None, temperature=0.3, role=None):
    """Return an LLM instance with optional role-based model routing.

    Provider is selected automatically:
    - Models prefixed with ``"openrouter/"`` are routed to **OpenRouter**
      using ``OPENROUTER_API_KEY`` (via the OpenAI-compatible API).
    - All other models are routed to **Groq** using ``GROQ_API_KEY``.

    Args:
        model: Explicit model name override. Takes priority over ``role``.
        temperature: Sampling temperature for generation.
        role: Agent role key (e.g. ``"planner"``, ``"researcher"``).
              When provided and ``model`` is *None*, the model is looked
              up from ``config.MODEL_CONFIG[role]``.

    Falls back to ``llama-3.3-70b-versatile`` (Groq) if neither ``model``
    nor ``role`` resolves to a valid model name.

    For Groq: reads a comma-separated list of API keys from ``GROQ_API_KEY``
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

    # ------------------------------------------------------------------ #
    # Route to OpenRouter when the model string starts with "openrouter/" #
    # ------------------------------------------------------------------ #
    if model.startswith("openrouter/"):
        # Strip the routing prefix before passing to the API
        actual_model = model[len("openrouter/"):]
        return _get_openrouter_llm(actual_model, temperature)

    # ------------------------------------------------------------------ #
    # Default: Groq                                                        #
    # ------------------------------------------------------------------ #
    keys_str = os.getenv("GROQ_API_KEY", "")
    keys = [k.strip() for k in keys_str.split(",") if k.strip()]

    if not keys:
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
