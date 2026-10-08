"""Optional core optimizer contract using deterministic reflection, no paid calls."""

import pytest


def test_installed_gepa_callable_reflection_and_string_candidate():
    oa = pytest.importorskip("gepa.optimize_anything")
    seen = []

    def reflect(prompt):
        seen.append(prompt)
        return "```\ngood\n```"

    def evaluate(candidate, example):
        assert example in {"training", "validation"}
        return float(candidate == "good"), {"failure": "Replace bad with good"}

    result = oa.optimize_anything(
        seed_candidate="bad",
        evaluator=evaluate,
        dataset=["training"],
        valset=["validation"],
        config=oa.GEPAConfig(
            engine=oa.EngineConfig(max_metric_calls=6),
            reflection=oa.ReflectionConfig(reflection_lm=reflect),
        ),
    )
    assert seen and result.best_candidate == "good"
