"""Reviewed heat-treatment planning knowledge for shaft process routes."""

from __future__ import annotations

from typing import Any

from rules.constants import HEAT_NAME, get_material_properties


class HeatTreatmentProvider:
    """Convert requirements into an explainable, conservative HT decision.

    The provider deliberately does not calculate furnace recipes.  Temperatures,
    soak times and quench media must remain drawing/specification controlled.
    It instead decides the process family, route constraints and the data that
    an engineer still needs to supply.
    """

    _PROFILES: dict[str, dict[str, Any]] = {
        "normalizing": {
            "process_name": "Normalizing",
            "route_description": "Normalize to refine grain and relieve internal stress.",
            "requires_datum_recovery": False,
            "requires_hard_finish": False,
        },
        "quench_temper": {
            "process_name": "Quench and Temper",
            "route_description": "Quench and temper to the drawing-specified mechanical properties.",
            "requires_datum_recovery": True,
            "requires_hard_finish": True,
        },
        "carburize_quench": {
            "process_name": "Carburize, Quench and Low-Temperature Temper",
            "route_description": "Carburize, quench and low-temperature temper; control case depth and distortion.",
            "requires_datum_recovery": True,
            "requires_hard_finish": True,
        },
        "nitriding": {
            "process_name": "Nitriding",
            "route_description": "Nitride to a hard wear-resistant case (surface hardness HV >= 900) with minimal distortion; low-temperature process, finishing by grinding after nitriding.",
            "requires_datum_recovery": True,
            "requires_hard_finish": True,
        },
        "induction_hardening": {
            "process_name": "Induction Hardening",
            "route_description": "Induction harden journals and local surfaces by high-frequency heating and quench; harden after semi-finish, finish by grinding.",
            "requires_datum_recovery": True,
            "requires_hard_finish": True,
        },
    }

    _PRE_TREATMENTS = {
        "normalizing": {
            "name": "Normalizing Pre-treatment",
            "description": "Normalize before final heat treatment to refine grain and reduce stress.",
        },
        "annealing": {
            "name": "Annealing Pre-treatment",
            "description": "Anneal before machining/final heat treatment as specified by the drawing.",
        },
        "stress_relief": {
            "name": "Stress-relief Heat Treatment",
            "description": "Relieve residual stress before final heat treatment as specified by the drawing.",
        },
    }

    def recommend(self, request: dict[str, Any], geometry: dict[str, Any]) -> dict[str, Any]:
        global_req = request.get("global_requirements", {})
        requested = global_req.get("heat_treatment", "none")
        heat_treatment = "quench_temper" if requested == "quench_and_temper" else requested
        material = str(request.get("material", ""))
        material_props = get_material_properties(material)
        high_precision = any(feature.get("high_precision") for feature in geometry.get("features", []))

        inputs = {
            "material": material,
            "requested_heat_treatment": requested,
            "blank_condition": global_req.get("blank_condition", "unknown"),
            "pre_heat_treatment": global_req.get("pre_heat_treatment", "auto"),
            "target_hardness_hrc": global_req.get("target_hardness_hrc"),
            "case_depth_mm": global_req.get("case_depth_mm"),
            "has_high_precision_feature": high_precision,
        }
        knowledge = {
            "material_recommendation": material_props.get("recommended_heat_treatment", "none"),
            "process_family": HEAT_NAME.get(heat_treatment, heat_treatment),
        }
        warnings: list[str] = []
        constraints: list[str] = []

        if heat_treatment == "none":
            if high_precision:
                warnings.append("High-precision feature has no heat treatment requirement; confirm drawing and service condition.")
            return self._result(
                heat_treatment="none", process_name=None, description=None,
                pre_treatment=None, requires_datum_recovery=False,
                requires_hard_finish=False, inputs=inputs, knowledge=knowledge,
                constraints=constraints, warnings=warnings,
            )

        profile = self._PROFILES.get(heat_treatment)
        if profile is None:
            warnings.append(f"Unsupported heat treatment type: {requested}. Engineer review is required.")
            return self._result(
                heat_treatment=heat_treatment, process_name=HEAT_NAME.get(heat_treatment, requested),
                description="Heat treatment details require engineer definition.", pre_treatment=None,
                requires_datum_recovery=True, requires_hard_finish=high_precision,
                inputs=inputs, knowledge=knowledge, constraints=constraints, warnings=warnings,
            )

        pre_treatment = self._resolve_pre_treatment(global_req)
        if pre_treatment:
            constraints.append("Pre-treatment must finish before final heat treatment.")
        if profile["requires_datum_recovery"]:
            constraints.append("Re-establish finishing datum after heat treatment before precision finishing.")
        if profile["requires_hard_finish"] and high_precision:
            constraints.append("Keep grinding/hard finishing allowance before heat treatment for high-precision surfaces.")
        if heat_treatment == "quench_temper" and global_req.get("target_hardness_hrc") is None:
            warnings.append("Target hardness is not specified; use drawing or material specification before release.")
        if heat_treatment == "carburize_quench":
            if global_req.get("target_hardness_hrc") is None:
                warnings.append("Surface hardness is not specified; use drawing or heat-treatment specification before release.")
            if global_req.get("case_depth_mm") is None:
                warnings.append("Effective case depth is not specified; engineer confirmation is required.")
        if heat_treatment == "nitriding" and global_req.get("target_hardness_hrc") is None:
            warnings.append("Nitriding target surface hardness is not specified; use drawing or nitriding specification (HV) before release.")
        if heat_treatment == "induction_hardening" and global_req.get("target_hardness_hrc") is None:
            warnings.append("Induction hardening target hardness is not specified; use drawing or specification before release.")
        recommended = material_props.get("recommended_heat_treatment", "none")
        if recommended not in ("none", heat_treatment):
            warnings.append(
                f"Requested {HEAT_NAME.get(heat_treatment, heat_treatment)} differs from the local material recommendation "
                f"({HEAT_NAME.get(recommended, recommended)}); drawing requirement takes precedence."
            )

        return self._result(
            heat_treatment=heat_treatment,
            process_name=profile["process_name"],
            description=profile["route_description"],
            pre_treatment=pre_treatment,
            requires_datum_recovery=profile["requires_datum_recovery"],
            requires_hard_finish=profile["requires_hard_finish"] and high_precision,
            inputs=inputs, knowledge=knowledge, constraints=constraints, warnings=warnings,
        )

    def _resolve_pre_treatment(self, global_req: dict[str, Any]) -> dict[str, str] | None:
        requested = global_req.get("pre_heat_treatment", "auto")
        if requested in self._PRE_TREATMENTS:
            return {"type": requested, **self._PRE_TREATMENTS[requested]}
        if requested == "auto" and global_req.get("blank_condition") == "forged":
            return {"type": "normalizing", **self._PRE_TREATMENTS["normalizing"]}
        return None

    @staticmethod
    def _result(
        *, heat_treatment: str, process_name: str | None, description: str | None,
        pre_treatment: dict[str, str] | None, requires_datum_recovery: bool,
        requires_hard_finish: bool, inputs: dict[str, Any], knowledge: dict[str, Any],
        constraints: list[str], warnings: list[str],
    ) -> dict[str, Any]:
        decision = {
            "heat_treatment": heat_treatment,
            "process_name": process_name,
            "description": description,
            "pre_treatment": pre_treatment,
            "requires_datum_recovery": requires_datum_recovery,
            "requires_hard_finish": requires_hard_finish,
        }
        return {
            **decision,
            "trace": {
                "inputs": inputs,
                "knowledge": knowledge,
                "constraints": constraints,
                "decision": decision,
                "warnings": warnings,
            },
        }
