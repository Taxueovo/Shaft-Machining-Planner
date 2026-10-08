"""GEPA candidate generation on train/validation only; never activate a candidate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/cases.synthetic.json")
    parser.add_argument("--seed-profile", type=Path)
    parser.add_argument("--max-evaluations", type=int, default=10)
    parser.add_argument(
        "--reflection-model",
        required=True,
        help="Reflection model name at the existing configured OpenAI-compatible endpoint",
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/evaluation/optimization")
    parser.add_argument(
        "--live", action="store_true", help="Permit model calls and provider charges"
    )
    args = parser.parse_args()
    if not args.live:
        parser.error(
            "Optimization requires explicit --live; rules-only scoring cannot train model prompts"
        )
    if not 1 <= args.max_evaluations <= 1000:
        parser.error("--max-evaluations must be between 1 and 1000")
    os.environ["JOB_DB_FILE"] = ":memory:"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["AGENT_MEMORY_ENABLED"] = "false"
    sys.path.insert(0, str(ROOT / "backend"))
    from evaluation.harness import load_dataset, run_case
    from llm_client import llm_available, chat
    from prompt_profiles import BASELINE, PromptProfile, load_profile, use_profile

    if not llm_available():
        parser.error("Configure an available model before optimization")
    try:
        import gepa.optimize_anything as oa
    except ImportError:
        parser.error(
            "Install GEPA in a separate optimization environment; see evaluation/README.md"
        )
    dataset = load_dataset(args.dataset)
    # Exclude frozen tests from the optimizer object itself, not just from its evaluator.
    tuning = dataset.model_copy(update={"cases": [c for c in dataset.cases if c.split != "test"]})
    if {c.split for c in tuning.cases} != {"train", "validation"}:
        parser.error("Optimization requires separate train and validation families")
    seed = load_profile(args.seed_profile) if args.seed_profile else BASELINE
    args.output_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    def reflect(prompt):
        messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
        return chat(messages, model=args.reflection_model, timeout_seconds=60)

    def evaluate(candidate: str, example) -> tuple[float, dict]:
        nonlocal count
        count += 1
        try:
            profile = PromptProfile.model_validate_json(candidate)
        except ValueError:
            oa.log("Candidate rejected: retain the PromptProfile JSON schema and registered roles.")
            return 0.0, {"failure": "Invalid PromptProfile JSON"}
        with use_profile(profile):
            report = run_case(example)
        (args.output_dir / f"trial-{count:04}.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        )
        feedback = {
            "failures": report["failures"],
            "feedback": report.get("feedback", {}),
            "case_id": report["case_id"],
            "model_usage": report["model_usage"],
        }
        oa.log(json.dumps(feedback, ensure_ascii=False))
        return float(report["passed"]), feedback

    result = oa.optimize_anything(
        seed_candidate=seed.model_dump_json(),
        evaluator=evaluate,
        dataset=[c for c in tuning.cases if c.split == "train"],
        valset=[c for c in tuning.cases if c.split == "validation"],
        objective="Improve bounded manufacturing agent behavior. Return only PromptProfile JSON "
        "with name, version and additions. Optimize guidance for existing registered roles. "
        "Preserve evidence requirements and engineering boundaries. Never change rules, tools, "
        "datasets, schemas or application code. Feedback is behavioral and not a machining qualification.",
        config=oa.GEPAConfig(
            engine=oa.EngineConfig(max_metric_calls=args.max_evaluations),
            reflection=oa.ReflectionConfig(reflection_lm=reflect),
        ),
    )
    candidate = PromptProfile.model_validate_json(result.best_candidate)
    path = args.output_dir / "candidate.json"
    path.write_text(candidate.model_dump_json(indent=2) + "\n")
    print(f"Candidate saved to {path}; evaluate against a frozen test baseline before review.")


if __name__ == "__main__":
    main()
