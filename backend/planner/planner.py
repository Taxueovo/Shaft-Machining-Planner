"""Planner orchestrator for the Process Engineering Methodology."""

from __future__ import annotations

from typing import Any, Dict

from .decision_node import DecisionNode
from ..workers.phase1_requirement_analysis import Phase1RequirementAnalysis
from ..workers.phase2_manufacturability import Phase2Manufacturability
from ..workers.phase3_process_planning import Phase3ProcessPlanning
from ..workers.phase4_resource_planning import Phase4ResourcePlanning
from ..workers.phase5_expert_review import Phase5ExpertReview
from ..workers.phase6_process_card_generation import Phase6ProcessCardGeneration


class Planner:
    """Composes six decision phases into a plan graph and executes them sequentially."""

    def __init__(self, knowledge: Dict[str, Any] | None = None) -> None:
        self.knowledge = knowledge or {}

        # Instantiate workers (could be swapped for DI in real implementation)
        self.phase1 = Phase1RequirementAnalysis()
        self.phase2 = Phase2Manufacturability()
        self.phase3 = Phase3ProcessPlanning()
        self.phase4 = Phase4ResourcePlanning()
        self.phase5 = Phase5ExpertReview()
        self.phase6 = Phase6ProcessCardGeneration()

    def plan(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run all phases in sequence and return a consolidated result."""
        context: Dict[str, Any] = {}
        # Phase 1
        out1 = self.phase1.run(input_data, context=context, knowledge=self.knowledge)
        context.update(out1.get("context", {}))
        # Phase 2
        out2 = self.phase2.run(input_data, context=context, knowledge=self.knowledge)
        context.update(out2.get("context", {}))
        # Phase 3
        out3 = self.phase3.run(input_data, context=context, knowledge=self.knowledge)
        context.update(out3.get("context", {}))
        # Phase 4
        out4 = self.phase4.run(input_data, context=context, knowledge=self.knowledge)
        context.update(out4.get("context", {}))
        # Phase 5
        out5 = self.phase5.run(input_data, context=context, knowledge=self.knowledge)
        context.update(out5.get("context", {}))
        # Phase 6
        out6 = self.phase6.run(input_data, context=context, knowledge=self.knowledge)
        context.update(out6.get("context", {}))

        result = {
            "phase1": out1,
            "phase2": out2,
            "phase3": out3,
            "phase4": out4,
            "phase5": out5,
            "phase6": out6,
            "final_output": context,
        }
        return result
