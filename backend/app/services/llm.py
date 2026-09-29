"""Provider-swappable LLM access (config only: LLM_PROVIDER / LLM_API_KEY / LLM_MODEL / LLM_BASE_URL).

providers:
  anthropic - Anthropic Messages API
  openai    - any OpenAI-compatible /chat/completions endpoint (OpenAI, Groq, Gemini-compat, Ollama, ...)
  none      - LLM disabled; callers fall back to the deterministic template

The LLM is used in exactly one place (generate_report) and only ever sees verified numbers.
"""
import httpx

from app.core import config

DEFAULT_MODELS = {"anthropic": "claude-haiku-4-5-20251001", "openai": "gpt-4o-mini"}


class LLMUnavailable(RuntimeError):
    pass


def is_configured() -> bool:
    return config.LLM_PROVIDER in ("anthropic", "openai") and bool(config.LLM_API_KEY)


def complete(system: str, user: str, max_tokens: int = 1400) -> str:
    if not is_configured():
        raise LLMUnavailable("LLM_PROVIDER/LLM_API_KEY not configured")
    model = config.LLM_MODEL or DEFAULT_MODELS[config.LLM_PROVIDER]
    try:
        if config.LLM_PROVIDER == "anthropic":
            r = httpx.post(
                (config.LLM_BASE_URL or "https://api.anthropic.com") + "/v1/messages",
                headers={"x-api-key": config.LLM_API_KEY, "anthropic-version": "2023-06-01"},
                json={"model": model, "max_tokens": max_tokens, "temperature": 0.2, "system": system,
                      "messages": [{"role": "user", "content": user}]},
                timeout=60,
            )
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json()["content"])
        r = httpx.post(
            (config.LLM_BASE_URL or "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json={"model": model, "temperature": 0.2, "max_tokens": max_tokens,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"LLM call failed: {exc}") from exc
