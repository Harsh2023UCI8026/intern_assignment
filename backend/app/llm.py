from __future__ import annotations

from typing import Any

_litellm: Any | None = None
_cache_ready = False


class SimulatedProviderFailure(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"Simulated provider response: HTTP {status_code}")
        self.status_code = status_code


def initialize_litellm() -> bool:
    """Configure LiteLLM's process-local cache once during app startup."""
    global _litellm, _cache_ready
    try:
        import litellm
        from litellm.caching import Cache

        litellm.cache = Cache(type="local")
        litellm.set_verbose = False
        _litellm = litellm
        _cache_ready = True
    except Exception:
        _litellm = None
        _cache_ready = False
    return _cache_ready


def cache_is_ready() -> bool:
    return _cache_ready


async def complete(
    *,
    model: str,
    api_key: str,
    messages: list[dict[str, str]],
    caching: bool,
    timeout_seconds: float,
    simulated_status: int | None = None,
) -> Any:
    if simulated_status is not None:
        raise SimulatedProviderFailure(simulated_status)
    if _litellm is None:
        raise RuntimeError("LiteLLM is not initialized")

    return await _litellm.acompletion(
        model=model,
        api_key=api_key,
        messages=messages,
        caching=caching,
        timeout=timeout_seconds,
    )


def is_cache_hit(response: Any) -> bool:
    hidden = getattr(response, "_hidden_params", None) or {}
    return bool(hidden.get("cache_hit"))


def response_text(response: Any) -> str:
    text = response.choices[0].message.content
    if isinstance(text, str):
        return text.strip()
    if isinstance(text, list):
        chunks = [
            item.get("text", "")
            for item in text
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "".join(chunks).strip()
    return ""


def token_usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    return (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


def provider_status_code(error: Exception) -> int | None:
    status = getattr(error, "status_code", None)
    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def is_retryable_provider_error(error: Exception) -> bool:
    status = provider_status_code(error)
    message = str(error).lower()
    return (
        status == 429
        or (status is not None and 500 <= status <= 599)
        or "quota" in message
        or "rate limit" in message
    )

