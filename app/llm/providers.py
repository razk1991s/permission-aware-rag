"""Provider implementations: local Ollama and a deterministic test stub."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time

import httpx

from app.config import settings
from app.llm.base import LLMError, LLMProvider, LLMResponse, Message

log = logging.getLogger(__name__)


# ==================================================================== Ollama
class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")

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
        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        # Ollama enforces schemas server-side, which is stronger than a prompt request.
        if json_schema is not None:
            payload["format"] = json_schema

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise LLMError(f"ollama: {exc}") from exc

        return LLMResponse(
            text=data.get("message", {}).get("content", ""),
            model=model,
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
            estimated_cost=0.0,  # Local provider; keep the field for cloud compatibility.
            finish_reason=data.get("done_reason", "stop"),
            raw=data,
        )

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                for text in texts:
                    resp = await client.post(
                        f"{self.base_url}/api/embeddings", json={"model": model, "prompt": text}
                    )
                    resp.raise_for_status()
                    vectors.append(resp.json()["embedding"])
        except httpx.HTTPError as exc:
            raise LLMError(f"ollama embeddings: {exc}") from exc
        return vectors

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                return (await client.get(f"{self.base_url}/api/tags")).status_code == 200
        except httpx.HTTPError:
            return False


# ====================================================================== Stub
class StubProvider(LLMProvider):
    """Deterministic provider for tests and CI, with no network or model.

    It does not and should not simulate real model quality. Its purpose is to
    test routing, retrieval, citations, approval gates, and injection defenses
    without a GPU or downloads. Quality measurements against it are meaningless,
    so the evaluation runner requires explicit permission to use it.
    """

    name = "stub"

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
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")

        if json_schema is not None:
            text = json.dumps(self._schema_shaped(json_schema, user), ensure_ascii=False)
        else:
            # Cite the first source injected into context so citation validation passes.
            sources = re.findall(r"id='(S\d+)'", user)
            first = sources[0] if sources else ""
            snippet = self._first_sentence(user)
            text = f"[stub] {snippet} [{first}]" if first else f"[stub] {snippet}"

        return LLMResponse(
            text=text,
            model=f"stub:{model}",
            prompt_tokens=sum(len(m["content"]) // 4 for m in messages),
            completion_tokens=len(text) // 4,
            latency_ms=1,
        )

    @staticmethod
    def _first_sentence(text: str) -> str:
        body = re.sub(r"<[^>]+>", " ", text)
        body = re.sub(r"\s+", " ", body).strip()
        return body[:160]

    @staticmethod
    def _schema_shaped(schema: dict, user: str) -> dict:
        """Build a minimal schema-shaped object so validation passes."""
        out: dict = {}
        for key, spec in (schema.get("properties") or {}).items():
            kind = spec.get("type")
            if kind == "string":
                out[key] = (spec.get("enum") or ["knowledge"])[0] if spec.get("enum") else "stub"
            elif kind == "array":
                out[key] = [user[:80]] if key.endswith("queries") else []
            elif kind == "boolean":
                out[key] = True
            elif kind in {"number", "integer"}:
                out[key] = 1
            elif kind == "object":
                out[key] = {}
        return out

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        return [self._hash_vector(t) for t in texts]

    @staticmethod
    def _hash_vector(text: str, dim: int | None = None) -> list[float]:
        """Deterministic bag-of-words vector normalized to L2.

        It has no semantic understanding; texts sharing words will be close.
        This is sufficient to test retrieval, RRF, and index behavior.
        """
        dim = dim or settings.embedding_dim
        vec = [0.0] * dim
        for token in re.findall(r"\w+", text.lower()):
            h = int(hashlib.blake2b(token.encode("utf-8"), digest_size=8).hexdigest(), 16)
            vec[h % dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]


def build_provider(name: str | None = None) -> LLMProvider:
    provider = (name or settings.llm_provider).lower()
    if provider == "ollama":
        return OllamaProvider()
    if provider == "stub":
        return StubProvider()
    raise ValueError(f"Unknown provider: {provider}")
