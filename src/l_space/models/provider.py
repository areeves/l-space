"""The only module that knows how to talk to a language model.

Everything else depends on the ``ModelProvider`` protocol, so the local backend can be
swapped (or faked in tests) without touching the rest of the application.
"""

from typing import Protocol

import httpx


class ModelProvider(Protocol):
    def generate(self, prompt: str, system: str | None = None) -> str: ...


class OllamaProvider:
    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str, system: str | None = None) -> str:
        payload: dict = {"model": self.model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        resp = httpx.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["response"]
