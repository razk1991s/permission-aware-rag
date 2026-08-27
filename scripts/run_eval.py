#!/usr/bin/env python3
"""מריץ את חבילת ההערכה ומדפיס טבלת השוואה.

    python scripts/run_eval.py                       # כל הקונפיגורציות
    python scripts/run_eval.py --config v3-hybrid-rerank
    python scripts/run_eval.py --gate                # יוצא עם שגיאה בחריגה משער
    python scripts/run_eval.py --save-baseline v5-full
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import dispose_engine, get_engine, run_migrations  # noqa: E402
from app.evaluation.gates import (  # noqa: E402
    evaluate_gates,
    gates_failed,
    load_baseline,
    render,
    save_baseline,
)
from app.evaluation.runner import CONFIGS, load_dataset, run_config  # noqa: E402

COLUMNS = [
    ("recall@5", "Recall@5"),
    ("mrr", "MRR"),
    ("context_precision", "CtxPrec"),
    ("refusal_accuracy", "Refusal"),
    ("permission_leak_rate", "Leak"),
    ("answer_correctness", "Correct"),
    ("p95_latency_ms", "p95 ms"),
]


def fmt(v):
    if v is None:
        return "—"
    return f"{v:.3f}" if isinstance(v, float) and v < 100 else f"{v:.0f}"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", action="append", choices=sorted(CONFIGS))
    ap.add_argument("--dataset", type=Path, default=None)
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--save-baseline", default=None, choices=sorted(CONFIGS))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--allow-stub", action="store_true",
                    help="מרשה הרצה מול ספק stub. המספרים חסרי משמעות — לבדיקת צנרת בלבד.")
    args = ap.parse_args()

    if settings.llm_provider == "stub" and not args.allow_stub:
        sys.exit(
            "ספק המודלים הוא stub. מדידת איכות מולו חסרת משמעות.\n"
            "הרץ מול ollama, או הוסף --allow-stub לבדיקת צנרת בלבד."
        )

    name, items = load_dataset(args.dataset)
    chosen = [CONFIGS[c] for c in (args.config or list(CONFIGS))]
    print(f"dataset: {name} · {len(items)} פריטים · ספק: {settings.llm_provider}\n")

    await run_migrations()
    engine = get_engine()
    results: dict[str, dict] = {}

    for cfg in chosen:
        async with engine.begin() as conn:
            out = await run_config(conn, cfg, items)
        results[cfg.name] = out["summary"]
        s = out["summary"]
        print(f"  {cfg.name:<20} pass={s['pass_rate']:.2f}  recall@5={fmt(s['recall@5'])}  "
              f"leak={fmt(s['permission_leak_rate'])}")

    # --- טבלת השוואה ---
    header = f"\n{'config':<20}" + "".join(f"{label:>12}" for _k, label in COLUMNS)
    print(header)
    print("-" * len(header))
    for cfg_name, s in results.items():
        row = f"{cfg_name:<20}" + "".join(f"{fmt(s.get(k)):>12}" for k, _l in COLUMNS)
        print(row)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nנשמר: {args.out}")

    if args.save_baseline:
        path = save_baseline(results[args.save_baseline], args.save_baseline)
        print(f"baseline נשמר: {path}")

    if args.gate:
        target = results.get("v5-full") or list(results.values())[-1]
        real_generation = settings.llm_provider != "stub"
        gate_results = evaluate_gates(target, load_baseline(), real_generation=real_generation)
        print("\nשערי איכות:")
        print(render(gate_results))
        if not real_generation:
            print(
                "\n⚠️  ספק stub: שערים תלויי־ייצור (סירובים, ציטוטים, הזיות) מדווחים\n"
                "   ואינם חוסמים — מול stub הערך שלהם אינו מודד את מה ששמו אומר.\n"
                "   מה שכן נאכף כאן: הרשאות, הזרקות, ואיכות השליפה."
            )
        failed = gates_failed(gate_results)
        if failed:
            print(f"\n{len(failed)} שערים חוסמים נכשלו.", file=sys.stderr)
            await dispose_engine()
            sys.exit(1)
        print("\nכל השערים החוסמים עברו.")

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
