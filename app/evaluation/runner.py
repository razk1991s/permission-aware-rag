"""Run the evaluation suite for a configuration and save the result.

Configuration comparison is the point: the same dataset and users are used
while one component changes at a time. This supports measurable claims about
improvements instead of vague statements that the system works well.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.agent.graph import build_graph
from app.agent.state import new_state
from app.config import ROOT, settings
from app.core.deps import resolve_allowed_doc_ids
from app.evaluation.metrics import (
    ItemScore,
    RunMetrics,
    answer_contains,
    citation_accuracy,
    context_precision,
    mrr,
    recall_at_k,
    refusal_correct,
)
from app.llm.gateway import get_gateway
from app.retrieval.pipeline import retrieve

log = logging.getLogger(__name__)

DATASET_PATH = ROOT / "data" / "eval" / "dataset.json"

ROLE_EMAILS = {
    "hr": "dana@meridian.local",
    "finance": "yuval@meridian.local",
    "support": "maya@meridian.local",
    "employee": "ori@meridian.local",
    "admin": "admin@meridian.local",
}


@dataclass
class EvalConfig:
    """One configuration to compare; see CONFIGS below."""

    name: str
    hybrid: bool = True
    rerank: bool = True
    understanding: bool = True
    retrieval_only: bool = False        # No generation model; inexpensive and deterministic.
    description: str = ""


CONFIGS: dict[str, EvalConfig] = {
    "v1-vector-only": EvalConfig("v1-vector-only", hybrid=False, rerank=False,
                                 understanding=False, retrieval_only=True,
                                 description="Baseline: vector search only"),
    "v2-hybrid": EvalConfig("v2-hybrid", hybrid=True, rerank=False, understanding=False,
                            retrieval_only=True, description="+ lexical search and RRF fusion"),
    "v3-hybrid-rerank": EvalConfig("v3-hybrid-rerank", hybrid=True, rerank=True,
                                   understanding=False, retrieval_only=True,
                                   description="+ cross-encoder reranking"),
    "v4-multiquery": EvalConfig("v4-multiquery", hybrid=True, rerank=True, understanding=True,
                                retrieval_only=True, description="+ query expansion"),
    "v5-full": EvalConfig("v5-full", hybrid=True, rerank=True, understanding=True,
                          retrieval_only=False, description="Full system with generation and validation"),
}


@dataclass
class EvalItem:
    id: str
    category: str
    question: str
    as_role: str
    expected_any: list[str] = field(default_factory=list)
    forbidden_any: list[str] = field(default_factory=list)
    relevant_docs: list[str] = field(default_factory=list)
    relevant_sections: list[str] = field(default_factory=list)
    expected_refusal: bool = False
    expects_tool: str | None = None
    forbidden_docs: list[str] = field(default_factory=list)
    # Items requiring model inference. Retrieval-only configurations have no
    # inference step, so running these would measure noise rather than quality.
    requires_generation: bool = False
    note: str | None = None


def load_dataset(path: Path | None = None) -> tuple[str, list[EvalItem]]:
    data = json.loads((path or DATASET_PATH).read_text(encoding="utf-8"))
    items = [
        EvalItem(
            id=i["id"],
            category=i["category"],
            question=i["question"],
            as_role=i["as_role"],
            expected_any=i.get("expected_any", []),
            forbidden_any=i.get("forbidden_any", []),
            relevant_docs=i.get("relevant_docs", []),
            relevant_sections=i.get("relevant_sections", []),
            expected_refusal=bool(i.get("expected_refusal", False)),
            expects_tool=i.get("expects_tool"),
            forbidden_docs=i.get("forbidden_docs", []),
            requires_generation=bool(i.get("requires_generation", False)),
            note=i.get("note"),
        )
        for i in data["items"]
    ]
    return data.get("name", "unnamed"), items


async def _user_for_role(conn: AsyncConnection, role: str) -> tuple[int, set[str], set[int]]:
    email = ROLE_EMAILS[role]
    row = (
        await conn.execute(
            text(
                """SELECT u.id,
                          COALESCE(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL), '{}') AS roles
                   FROM users u
                   LEFT JOIN user_roles ur ON ur.user_id = u.id
                   LEFT JOIN roles r ON r.id = ur.role_id
                   WHERE u.email = :email GROUP BY u.id"""
            ),
            {"email": email},
        )
    ).first()
    if row is None:
        raise RuntimeError(f"Demo user {email} not found - run seed_auth.sql")
    allowed = await resolve_allowed_doc_ids(conn, row.id)
    return row.id, set(row.roles), set(allowed)


def _relevance_hit(cand, item: EvalItem) -> bool:
    if item.relevant_docs and cand.doc_id not in item.relevant_docs:
        return False
    if item.relevant_sections:
        path = cand.section_path or ""
        return any(s in path for s in item.relevant_sections)
    return bool(item.relevant_docs)


async def run_config(
    conn: AsyncConnection,
    config: EvalConfig,
    items: list[EvalItem],
    *,
    save: bool = True,
) -> dict:
    metrics = RunMetrics()
    gateway = get_gateway()

    for item in items:
        if config.retrieval_only and item.requires_generation:
            continue
        user_id, roles, allowed = await _user_for_role(conn, item.as_role)
        scores: dict[str, float] = {}
        notes: list[str] = []
        started = time.perf_counter()

        # ---------- Retrieval layer ----------
        result = await retrieve(
            conn,
            user_id=user_id,
            question=item.question,
            hybrid=config.hybrid,
            use_rerank=config.rerank,
            use_understanding=config.understanding,
            gateway=gateway,
        )
        ranked = result.candidates
        hits = [1 if _relevance_hit(c, item) else 0 for c in ranked]
        retrieved_ids = list(range(len(ranked)))
        relevant_ids = [i for i, h in enumerate(hits) if h]

        if item.relevant_docs:
            scores["recall@5"] = recall_at_k(retrieved_ids, relevant_ids, k=5)
            scores["mrr"] = mrr(retrieved_ids, relevant_ids)
            scores["context_precision"] = context_precision(retrieved_ids, relevant_ids, k=5)

        # ---------- Answer layer ----------
        if config.retrieval_only:
            # Retrieval-only mode has no generation model. Mark refusal by the
            # threshold so authorization items can still be evaluated.
            refused = result.below_threshold or not ranked
            answer_text = " ".join(c.content for c in ranked[:3])
            cited_ids: list[int] = []
            served_ids = [c.chunk_id for c in ranked]
            tools: list[dict] = []
        else:
            state = new_state(
                question=item.question,
                user_id=user_id,
                roles=roles,
                allowed_doc_ids=allowed,
                thread_id=f"eval-{item.id}",
            )
            final = await build_graph(conn, gateway).ainvoke(state)
            refused = bool(final.get("refused"))
            answer_text = final.get("answer") or ""
            cited_ids = [c["chunk_id"] for c in (final.get("citations") or [])]
            served_ids = cited_ids
            tools = final.get("tool_results") or []
            if final.get("groundedness") is not None:
                scores["groundedness"] = float(final["groundedness"])
            scores["hallucination"] = 1.0 if final.get("hallucination_flag") else 0.0

        # A document that should never have been retrieved, such as an expired version.
        forbidden_docs_hit = sorted(
            {c.doc_id for c in ranked if c.doc_id in item.forbidden_docs}
        )

        scores["latency_ms"] = (time.perf_counter() - started) * 1000
        scores["refusal_correct"] = refusal_correct(refused, item.expected_refusal)
        if cited_ids:
            scores["citation_accuracy"] = citation_accuracy(cited_ids, served_ids)

        # ---------- Pass/fail decision ----------
        passed = True
        leaked = False

        if forbidden_docs_hit:
            passed = False
            leaked = True
            notes.append(f"Forbidden document retrieved: {forbidden_docs_hit}")

        if item.expected_refusal:
            # Authorization cases must refuse without leaking sensitive values.
            if not refused:
                passed = False
                notes.append("The system should have refused")
            hits = [f for f in item.forbidden_any if f.replace(",", "") in answer_text.replace(",", "")]
            if hits:
                passed = False
                leaked = True
                notes.append(f"Forbidden value leaked: {hits}")
        else:
            if refused:
                passed = False
                notes.append("Incorrect refusal")
            elif item.expected_any:
                correct = answer_contains(answer_text, item.expected_any)
                scores["answer_correctness"] = correct
                if not correct:
                    passed = False
                    notes.append(f"None of {item.expected_any} found")
            forbidden = [f for f in item.forbidden_any if f.replace(",", "") in answer_text.replace(",", "")]
            if forbidden:
                passed = False
                leaked = True
                notes.append(f"Forbidden value appeared: {forbidden}")
            if (
                item.expects_tool
                and not config.retrieval_only
                and not any(
                    t.get("tool") == item.expects_tool and t.get("status") == "ok" for t in tools
                )
            ):
                passed = False
                notes.append(f"Tool {item.expects_tool} did not run successfully")

        metrics.add(
            ItemScore(item.id, item.category, passed, scores, "; ".join(notes) or None, leaked)
        )

    summary = metrics.summary()
    summary["config"] = config.name
    summary["retrieval_only"] = config.retrieval_only
    summary["provider"] = settings.llm_provider

    if save:
        await _save_run(conn, config, summary, metrics)
    return {"summary": summary, "items": [i.__dict__ for i in metrics.items]}


async def _save_run(conn, config: EvalConfig, summary: dict, metrics: RunMetrics) -> None:
    row = (
        await conn.execute(
            text(
                """INSERT INTO eval_runs (config_name, config, metrics)
                   VALUES (:name, CAST(:config AS jsonb), CAST(:metrics AS jsonb))
                   RETURNING id"""
            ),
            {
                "name": config.name,
                "config": json.dumps(config.__dict__, ensure_ascii=False),
                "metrics": json.dumps(summary, ensure_ascii=False),
            },
        )
    ).first()
    for item in metrics.items:
        await conn.execute(
            text(
                """INSERT INTO eval_results (run_id, answer, scores, passed)
                   VALUES (:run, :answer, CAST(:scores AS jsonb), :passed)"""
            ),
            {
                "run": row.id,
                "answer": item.notes,
                "scores": json.dumps({"item": item.item_id, **item.scores}, ensure_ascii=False),
                "passed": item.passed,
            },
        )
