"""חוזה אחיד לספקי מודלים.

כל קריאה למודל במערכת עוברת דרך הממשק הזה, ודרך השער ב-app/llm/gateway.py.
המשמעות המעשית: החלפת Ollama ב-Azure OpenAI היא הוספת מימוש אחד, בלי לגעת
בשום קוד שקורא למודל.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

Message = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": ...}


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    estimated_cost: float = 0.0
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMError(RuntimeError):
    """כשל שניתן לנסות שוב מולו — timeout, מודל לא זמין, שגיאת רשת."""


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        json_schema: dict | None = None,
        timeout: float = 120.0,
    ) -> LLMResponse:
        ...

    @abstractmethod
    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        ...

    async def health(self) -> bool:
        return True
