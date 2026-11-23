"""A scripted stand-in for the language model."""

from typing import Any


class FakeProvider:
    """Replies to plan requests (schema given) from ``plans`` in order, and to writing requests
    with ``text``. Records every call so tests can check what was (not) sent."""

    def __init__(self, plans: list[str] | str, text: str = "") -> None:
        self.plans = [plans] if isinstance(plans, str) else list(plans)
        self.text = text
        self.plan_calls: list[str] = []
        self.write_calls: list[str] = []
        self.max_tokens: list[int | None] = []

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.max_tokens.append(max_tokens)
        if schema is not None:
            self.plan_calls.append(prompt)
            return self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        self.write_calls.append(prompt)
        return self.text
