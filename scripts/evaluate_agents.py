"""Offline by default; live model calls require an explicit --live flag."""

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
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--split", choices=["train", "validation", "test", "all"], default="all")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--baseline", type=Path, help="Compare against an existing report")
    parser.add_argument("--output", type=Path, default=ROOT / "output/evaluation/run.json")
    args = parser.parse_args()
    if not args.live:
        os.environ["LLM_PROVIDER"] = "rules"
    os.environ["JOB_DB_FILE"] = ":memory:"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["AGENT_MEMORY_ENABLED"] = "false"
    sys.path.insert(0, str(ROOT / "backend"))
    from evaluation.harness import load_dataset, run_suite, compare_runs
    from llm_client import llm_available
    from prompt_profiles import BASELINE, load_profile

    if args.live and not llm_available():
        parser.error("--live requires a configured, available local or remote model")
    profile = load_profile(args.profile) if args.profile else BASELINE
    splits = {"train", "validation", "test"} if args.split == "all" else {args.split}
    report = run_suite(load_dataset(args.dataset), profile, splits, args.repeats)
    if args.baseline:
        report["comparison"] = compare_runs(json.loads(args.baseline.read_text()), report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "case_count": report["case_count"],
                "evidence_level": report["evidence_level"],
                "output": str(args.output),
                "comparison": report.get("comparison"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if not report["badcases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
