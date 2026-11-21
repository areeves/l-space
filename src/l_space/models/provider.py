"""The only module that knows how to talk to a language model.

Everything else depends on the ``ModelProvider`` protocol, so the local backend can be
swapped (or faked in tests) without touching the rest of the application.
"""

from typing import Any, Protocol

import httpx

from l_space.core.settings import Settings


class ModelProvider(Protocol):
    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Return the model's reply. With ``schema``, the reply is constrained to that JSON
        schema. ``max_tokens`` caps the length of the reply."""
        ...


class OllamaProvider:
    def __init__(self, base_url: str, model: str, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        options: dict[str, Any] = {"temperature": 0}  # repeatable answers and eval runs
        if max_tokens:
            options["num_predict"] = max_tokens
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if system:
            payload["system"] = system
        if schema:
            payload["format"] = schema
        resp = httpx.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["response"]


def from_settings(settings: Settings) -> OllamaProvider:
    return OllamaProvider(settings.ollama_url, settings.ollama_model, settings.ollama_timeout)
