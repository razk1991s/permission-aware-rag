"""Model gateway - the only exit point for model calls.

Every call passes through here, so task routing, quotas, PII removal, fallback,
and cost tracking live here. See ADR 0007.

The `judge` task is deliberately routed to a different model from the one
that generated the answer. A model asked to judge its own output tends to approve it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from app.config import settings
from app.llm.base import LLMError, LLMProvider, LLMResponse, Message
from app.llm.providers import build_provider
from app.security.pii import redact

log = logging.getLogger(__name__)


@dataclass
class ModelRoute:
    primary: str
    fallback: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.1


class QuotaExceeded(RuntimeError):
    pass


class TokenQuota:
    """מכסה יומית פשוטה, בזיכרון.

    בפרודקשן זו טבלה או Redis; כאן המנגנון הוא מה שמודגם. הוא מתאפס
    בהפעלה מחדש, וזה מתועד ולא מוסתר.
    """

    def __init__(self, daily_limit: int) -> None:
        self.daily_limit = daily_limit
        self._used: dict[tuple[int, date], int] = defaultdict(int)

    def check(self, user_id: int | None) -> None:
        if user_id is None or self.daily_limit <= 0:
            return
        used = self._used[(user_id, date.today())]
        if used >= self.daily_limit:
            raise QuotaExceeded(f"נוצלה המכסה היומית ({self.daily_limit} טוקנים)")

    def record(self, user_id: int | None, tokens: int) -> None:
        if user_id is not None:
            self._used[(user_id, date.today())] += tokens

    def used_by(self, user_id: int) -> int:
        return self._used[(user_id, date.today())]


class LLMGateway:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or build_provider()
        self.quota = TokenQuota(settings.daily_token_quota)
        # מגביל מקביליות: מודל מקומי הוא משאב יחיד. בלי זה, חמש בקשות
        # במקביל יגרמו לכולן לחכות ולכולן להיכשל ב-timeout.
        self._sem = asyncio.Semaphore(settings.llm_max_concurrency)
        self.calls: list[dict] = []

    # ------------------------------------------------------------- ניתוב
    @property
    def routes(self) -> dict[str, ModelRoute]:
        g, u = settings.generation_model, settings.utility_model
        return {
            "generation": ModelRoute(g, fallback=u, max_tokens=1024, temperature=0.1),
            "understanding": ModelRoute(u, fallback=g, max_tokens=384, temperature=0.0),
            "routing": ModelRoute(u, fallback=g, max_tokens=128, temperature=0.0),
            "judge": ModelRoute(settings.judge_model, fallback=u, max_tokens=512, temperature=0.0),
            "extract": ModelRoute(u, fallback=g, max_tokens=384, temperature=0.0),
        }

    def resolve(self, task: str) -> ModelRoute:
        return self.routes.get(task, self.routes["generation"])

    # ------------------------------------------------------------- קריאה
    async def complete(
        self,
        *,
        task: str,
        messages: list[Message],
        user_id: int | None = None,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> LLMResponse:
        route = self.resolve(task)
        self.quota.check(user_id)

        if settings.redact_pii:
            messages = [{**m, "content": redact(m["content"])} for m in messages]

        started = time.perf_counter()
        async with self._sem:
            try:
                resp = await self.provider.complete(
                    messages,
                    model=route.primary,
                    temperature=route.temperature if temperature is None else temperature,
                    max_tokens=max_tokens or route.max_tokens,
                    json_schema=json_schema,
                    timeout=timeout or settings.llm_timeout_seconds,
                )
            except LLMError as exc:
                if not route.fallback:
                    raise
                log.warning("task=%s primary=%s failed (%s) — falling back to %s",
                            task, route.primary, exc, route.fallback)
                resp = await self.provider.complete(
                    messages,
                    model=route.fallback,
                    temperature=route.temperature if temperature is None else temperature,
                    max_tokens=max_tokens or route.max_tokens,
                    json_schema=json_schema,
                    timeout=timeout or settings.llm_timeout_seconds,
                )

        resp.latency_ms = resp.latency_ms or int((time.perf_counter() - started) * 1000)
        self.quota.record(user_id, resp.total_tokens)
        self.calls.append(
            {
                "task": task,
                "model": resp.model,
                "prompt_tokens": resp.prompt_tokens,
                "completion_tokens": resp.completion_tokens,
                "latency_ms": resp.latency_ms,
                "cost": resp.estimated_cost,
            }
        )
        return resp

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with self._sem:
            return await self.provider.embed(texts, model=settings.embedding_model)

    async def health(self) -> bool:
        return await self.provider.health()

    def usage(self) -> dict:
        return {
            "calls": len(self.calls),
            "prompt_tokens": sum(c["prompt_tokens"] for c in self.calls),
            "completion_tokens": sum(c["completion_tokens"] for c in self.calls),
            "estimated_cost": round(sum(c["cost"] for c in self.calls), 6),
            "by_task": {
                task: sum(1 for c in self.calls if c["task"] == task)
                for task in {c["task"] for c in self.calls}
            },
        }


_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway


def reset_gateway() -> None:
    """לשימוש בבדיקות בלבד."""
    global _gateway
    _gateway = None
