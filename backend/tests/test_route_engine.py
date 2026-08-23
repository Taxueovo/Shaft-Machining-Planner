"""Process route rule engine tests (subset of DEF-TEST-01)."""

from rules import build_route
from models.process import ProcessStage


def _make_request(**kwargs):
    defaults = {
        "material": "45",
        "blank_diameter_mm": 50,
        "segments": [
            {"segment_id": "S1", "diameter_mm": 30, "length_mm": 100},
        ],
        "features": [],
        "global_requirements": {
            "heat_treatment": "none",
            "surface_treatment": "none",
            "batch_quantity": 1,
        },
    }
    defaults.update(kwargs)
    return defaults


def _make_geometry(request):
    segments = []
    cursor = 0.0
    for seg in request["segments"]:
        item = dict(seg)
        item["global_start_mm"] = round(cursor, 3)
        cursor += float(seg["length_mm"])
        item["global_end_mm"] = round(cursor, 3)
        item["high_precision"] = False
        segments.append(item)
    return {
        "total_length_mm": round(cursor, 3),
        "max_finished_diameter_mm": max(s["diameter_mm"] for s in segments),
        "blank_diameter_mm": request["blank_diameter_mm"],
        "segments": segments,
        "features": request.get("features", []),
        "warnings": [],
    }


class TestBasicRoute:
    def test_no_heat_treatment(self):
        req = _make_request()
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        assert "Blanking" in names
        assert "Face Turning" in names
        assert "Rough Turning" in names
        assert "Finish Turning" in names
        assert "Final Inspection" in names
        assert "Heat Treatment" not in names

    def test_with_heat_treatment(self):
        req = _make_request(
            global_requirements={
                "heat_treatment": "normalizing",
                "surface_treatment": "none",
                "batch_quantity": 1,
            }
        )
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        stages = [op["stage"] for op in route]
        assert "Heat Treatment" in names
        assert "Repair Center Holes" in names
        assert ProcessStage.heat_treatment.value in stages
        assert ProcessStage.datum_recovery.value in stages

    def test_quench_temper_does_not_assume_normalizing(self):
        req = _make_request(
            global_requirements={
                "heat_treatment": "quench_temper",
                "surface_treatment": "none",
                "batch_quantity": 1,
            }
        )
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        assert "Normalizing Pre-treatment" not in names
        assert "Heat Treatment" in names

    def test_surface_treatment(self):
        req = _make_request(
            global_requirements={
                "heat_treatment": "none",
                "surface_treatment": "blackening",
                "batch_quantity": 1,
            }
        )
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        stages = [op["stage"] for op in route]
        assert ProcessStage.surface_treatment.value in stages

    def test_operation_no_sequential(self):
        req = _make_request()
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        # Operation numbers increment from 1
        nos = [op["operation_no"] for op in route]
        assert nos == list(range(1, len(route) + 1)), (
            f"Operation numbers are not consecutive integers starting from 1: {nos}"
        )


class TestHighPrecisionFeatures:
    def _make_hp_feature(self, timing="before_heat_treatment"):
        return {
            "feature_id": "F1",
            "feature_type": "keyway",
            "positioning_mode": "global_absolute",
            "global_position_mm": 50,
            "tolerance_upper_mm": 0.01,
            "tolerance_lower_mm": -0.01,
            "roughness_ra": 0.4,
            "processing_timing": timing,
            "keyway_width_mm": 10,
            "keyway_depth_mm": 5,
            "feature_length_mm": 50,
            "high_precision": True,
            "resolved_segment_id": "S1",
        }

    def test_hp_before_heat_no_post_heat_finishing(self):
        """High-precision keyway finished before Heat Treatment -> no post-heat-treatment finishing operations should appear (DEF-PROC-03)."""
        feature = self._make_hp_feature(timing="before_heat_treatment")
        req = _make_request(
            global_requirements={
                "heat_treatment": "normalizing",
                "surface_treatment": "none",
                "batch_quantity": 1,
            },
            features=[feature],
        )
        geom = _make_geometry(req)
        geom["features"] = [feature]
        route = build_route(req, geom, {"F1": "before_heat_treatment"})
        # Should not contain operations in the feature_after_heat stage
        post_heat_feature_ops = [
            op for op in route if op["stage"] == ProcessStage.feature_after_heat.value
        ]
        assert len(post_heat_feature_ops) == 0, (
            f"before_heat_treatment was chosen but post-heat-treatment finishing appeared: {post_heat_feature_ops}"
        )

    def test_hp_split_mode_has_rough_and_finish(self):
        """High-precision keyway machined both before and after heat treatment -> should have rough and finish operations (DEF-PROC-02)."""
        feature = self._make_hp_feature(timing="before_and_after_heat_treatment")
        req = _make_request(
            global_requirements={
                "heat_treatment": "normalizing",
                "surface_treatment": "none",
                "batch_quantity": 1,
            },
            features=[feature],
        )
        geom = _make_geometry(req)
        geom["features"] = [feature]
        route = build_route(req, geom, {"F1": "before_and_after_heat_treatment"})
        feature_ops = [op for op in route if op.get("feature_id") == "F1"]
        names = [op["name"] for op in feature_ops]
        # Should have Rough mill keyway and Precision grind keyway
        assert "Rough mill keyway" in names, f"Missing Rough mill keyway, actual: {names}"
        assert "Precision grind keyway" in names, f"Missing Precision grind keyway, actual: {names}"

    def test_no_heat_hp_feature_after_finish(self):
        """High-precision feature without Heat Treatment -> scheduled after finish turning."""
        feature = self._make_hp_feature(timing="undecided")
        feature["high_precision"] = True
        req = _make_request(features=[feature])
        geom = _make_geometry(req)
        geom["features"] = [feature]
        route = build_route(req, geom, {})
        # Should not contain feature_before_heat operations (high precision without heat treatment should be scheduled later)
        pre_heat_feature_ops = [
            op
            for op in route
            if op.get("feature_id") == "F1"
            and op["stage"] == ProcessStage.feature_before_heat.value
        ]
        assert len(pre_heat_feature_ops) == 0
        post_finish_feature_ops = [
            op
            for op in route
            if op.get("feature_id") == "F1"
            and op["stage"] == ProcessStage.feature_before_inspection.value
        ]
        assert [op["name"] for op in post_finish_feature_ops] == ["Precision mill keyway"]


class TestFeatureScheduling:
    def _make_feature(self, feature_type, **fields):
        feature = {
            "feature_id": "F1",
            "feature_type": feature_type,
            "global_position_mm": 30,
            "high_precision": False,
            "processing_timing": "undecided",
        }
        feature.update(fields)
        return feature

    def _route_for(self, feature, heat="none"):
        req = _make_request(
            features=[feature],
            global_requirements={
                "heat_treatment": heat,
                "surface_treatment": "none",
                "batch_quantity": 1,
            },
        )
        geom = _make_geometry(req)
        geom["features"] = [feature]
        return build_route(req, geom, {})

    def test_standard_keyway_is_machined_after_finish_turning(self):
        route = self._route_for(self._make_feature("keyway"))
        keyway = next(op for op in route if op.get("feature_id") == "F1")
        finish = next(op for op in route if op["name"] == "Finish Turning")
        assert keyway["name"] == "Mill keyway"
        assert keyway["stage"] == ProcessStage.feature_before_inspection.value
        assert keyway["operation_no"] > finish["operation_no"]

    def test_precision_keyway_defaults_to_split_route_with_heat_treatment(self):
        feature = self._make_feature("keyway", high_precision=True)
        route = self._route_for(feature, heat="quench_temper")
        feature_ops = [op for op in route if op.get("feature_id") == "F1"]
        assert [op["name"] for op in feature_ops] == ["Rough mill keyway", "Precision grind keyway"]
        assert [op["stage"] for op in feature_ops] == [
            ProcessStage.feature_before_heat.value,
            ProcessStage.feature_after_heat.value,
        ]

    def test_precision_bearing_seat_uses_turning_then_grinding(self):
        feature = self._make_feature(
            "bearing_seat",
            high_precision=True,
            bearing_seat_tolerance="IT6",
        )
        route = self._route_for(feature, heat="quench_temper")
        feature_ops = [op for op in route if op.get("feature_id") == "F1"]
        assert [(op["name"], op["process_category"]) for op in feature_ops] == [
            ("Rough turn bearing seat", "ISO Turning"),
            ("Precision grind bearing seat", "Cylindrical Grinding"),
        ]

    def test_flange_holes_get_a_separate_drilling_operation(self):
        feature = self._make_feature("flange", flange_holes=4)
        route = self._route_for(feature)
        feature_ops = [op for op in route if op.get("feature_id") == "F1"]
        assert [(op["name"], op["process_category"]) for op in feature_ops] == [
            ("Finish turn flange", "ISO Turning"),
            ("Drill flange bolt holes", "Drilling"),
        ]

    def test_undecided_hp_nonsplit_feature_scheduled_once(self):
        """High-precision non-splittable feature with undecided timing + heat must NOT be double-scheduled.

        The inference step (soft-state block) schedules the feature before heat treatment, so the
        post-finish block must not schedule it again (regression for the double-scheduling bug).
        """
        feature = self._make_feature("flange", high_precision=True)
        route = self._route_for(feature, heat="quench_temper")
        feature_ops = [op for op in route if op.get("feature_id") == "F1"]
        assert len(feature_ops) == 1, (
            f"Expected exactly one F1 operation, got {len(feature_ops)}: "
            f"{[(op['name'], op['stage']) for op in feature_ops]}"
        )
        assert feature_ops[0]["stage"] == ProcessStage.feature_before_heat.value


class TestGrinding:
    def test_grinding_added_for_tight_tolerance(self):
        """Segments with Ra<=0.4 or tolerance <=0.01mm should have a finish grinding operation."""
        req = _make_request(
            segments=[
                {
                    "segment_id": "S1",
                    "diameter_mm": 30,
                    "length_mm": 100,
                    "diameter_upper_deviation_mm": 0.005,
                    "diameter_lower_deviation_mm": -0.005,
                }
            ]
        )
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        stages = [op["stage"] for op in route]
        assert ProcessStage.precision_finish.value in stages

    def test_no_grinding_for_normal_tolerance(self):
        req = _make_request(
            segments=[
                {
                    "segment_id": "S1",
                    "diameter_mm": 30,
                    "length_mm": 100,
                    "diameter_upper_deviation_mm": 0.05,
                    "diameter_lower_deviation_mm": -0.05,
                }
            ]
        )
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        stages = [op["stage"] for op in route]
        assert ProcessStage.precision_finish.value not in stages


class TestStageAndCoverageConsistency:
    """Fix: the deburr stage of Chamfer & Deburr + main bore feature coverage (Feature Coverage / Topology Sort previously failed)."""

    def test_all_route_stages_are_valid_process_stage(self):
        """Every operation stage must be a valid ProcessStage member (deburr was once omitted, causing Topology Sort failure)."""
        valid = {s.value for s in ProcessStage}
        feature = {
            "feature_id": "F1",
            "feature_type": "gear_teeth",
            "positioning_mode": "global_absolute",
            "global_position_mm": 50,
            "high_precision": False,
            "gear_module": 3.5,
            "gear_teeth": 87,
            "gear_face_width_mm": 40.0,
            "gear_pressure_angle": 20.0,
        }
        req = _make_request(features=[feature])
        geom = _make_geometry(req)
        geom["features"] = [feature]
        route = build_route(req, geom, {})
        names = {op["name"] for op in route}
        assert "Chamfer & Deburr" in names, "Expected Chamfer & Deburr in route"
        for op in route:
            assert op["stage"] in valid, (
                f"op {op['operation_no']} ({op['name']}) has invalid stage {op['stage']!r}"
            )
        # The deburr stage must be present
        assert ProcessStage.deburr.value in {op["stage"] for op in route}

    def test_main_bore_feature_covered_by_blank_boring(self):
        """The hollow blank's main bore is covered by blank-stage rough/finish boring: Rough/Finish Boring
        must carry the bore's feature_id, otherwise Feature Coverage would falsely report the main bore as uncovered."""
        bore = {
            "feature_id": "F3",
            "feature_type": "bore",
            "positioning_mode": "global_absolute",
            "global_position_mm": 0,
            "high_precision": False,
            "bore_diameter_mm": 25.0,
            "bore_length_mm": 100.0,
            "bore_through": True,
        }
        req = _make_request(
            blank_type="hollow",
            blank_inner_diameter_mm=25.0,
            features=[bore],
        )
        geom = _make_geometry(req)
        geom["features"] = [bore]
        route = build_route(req, geom, {})
        boring_ops = [op for op in route if op["name"] in ("Rough Boring", "Finish Boring")]
        assert len(boring_ops) == 2, (
            f"Expected Rough+Finish Boring, got {[op['name'] for op in boring_ops]}"
        )
        for op in boring_ops:
            assert op.get("feature_id") == "F3", (
                f"{op['name']} should carry main bore feature_id F3, got {op.get('feature_id')}"
            )
        # Do not duplicate feature-level boring operations
        assert "Bore" not in [op["name"] for op in route]


class TestCarburizedGearShaftRoute:
    """Carburized quench-hardened gear shaft: generated per the real production route (front-loaded turning + carburize-quench chain + gear/OD grinding)."""

    def _carburized_request(self):
        gear = {
            "feature_id": "F1",
            "feature_type": "gear_teeth",
            "positioning_mode": "global_absolute",
            "global_position_mm": 50,
            "high_precision": False,
            "gear_module": 3.5,
            "gear_teeth": 87,
            "gear_face_width_mm": 40.0,
            "gear_pressure_angle": 20.0,
        }
        req = _make_request(
            global_requirements={
                "heat_treatment": "carburize_quench",
                "surface_treatment": "none",
                "target_hardness_hrc": 60,
                "case_depth_mm": 1.2,
                "batch_quantity": 1,
            },
            features=[gear],
        )
        geom = _make_geometry(req)
        geom["features"] = [gear]
        return req, geom

    def test_carburized_route_has_full_production_chain(self):
        req, geom = self._carburized_request()
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        expected_chain = [
            "Hob gear",
            "Gear Chamfer",
            "Pre-Clean",
            "Heat Treatment",
            "Clean Quench Oil",
            "Temper",
            "Shot Blast",
            "Heat-treatment Inspection",
            "Center-hole Chamfer Grinding",
            "External Cylindrical Grinding",
            "Shot Peening",
            "Precision grind gear teeth",
            "Laser Marking",
            "Magnetic Particle Inspection",
        ]
        idx = -1
        for name in expected_chain:
            found = next((i for i, n in enumerate(names) if n == name and i > idx), None)
            assert found is not None, f"Expected '{name}' after position {idx}, got chain {names}"
            idx = found
        assert "Finish Turning" in names
        assert "Final Inspection" in names

    def test_carburized_finish_turning_before_heat(self):
        req, geom = self._carburized_request()
        route = build_route(req, geom, {})
        finish = next(op for op in route if op["name"] == "Finish Turning")
        ht = next(op for op in route if op["name"] == "Heat Treatment")
        assert finish["stage"] == ProcessStage.finish_before_heat.value
        assert finish["operation_no"] < ht["operation_no"]

    def test_carburized_route_all_stages_valid(self):
        req, geom = self._carburized_request()
        route = build_route(req, geom, {})
        valid = {s.value for s in ProcessStage}
        for op in route:
            assert op["stage"] in valid, f"invalid stage {op['stage']!r}"

    def test_non_carburized_route_unaffected(self):
        # A plain route (no heat treatment) still follows the original logic and must not contain the carburizing chain
        req = _make_request()
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        assert "Pre-Clean" not in names
        assert "Laser Marking" not in names
        assert "Magnetic Particle Inspection" not in names


class TestCamshaftRoute:
    """Camshaft: quench and temper + local induction hardening of cams -> rough grind / CBN finish grind cam chain."""

    def _cam_request(self, heat="quench_temper"):
        cam = {
            "feature_id": "F1",
            "feature_type": "cam",
            "positioning_mode": "global_absolute",
            "global_position_mm": 50,
            "high_precision": True,
            "feature_length_mm": 30.0,
            "cam_type": "grinding",
            "cam_lobe_count": 4,
            "cam_base_circle_diameter_mm": 40.0,
            "cam_lobe_lift_mm": 8.0,
        }
        req = _make_request(
            global_requirements={
                "heat_treatment": heat,
                "surface_treatment": "none",
                "target_hardness_hrc": 55,
                "batch_quantity": 1,
            },
            features=[cam],
        )
        geom = _make_geometry(req)
        geom["features"] = [cam]
        return req, geom

    def test_camshaft_route_has_cam_production_chain(self):
        req, geom = self._cam_request()
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        expected_chain = [
            "Rough Grind OD",
            "Rough Grind Cam Lobe",
            "Induction Harden Cam Lobe",
            "CBN Finish Grind Journals",
            "CBN Finish Grind Cam Lobe",
            "Polish",
            "Magnetic Particle Inspection",
        ]
        idx = -1
        for name in expected_chain:
            found = next((i for i, n in enumerate(names) if n == name and i > idx), None)
            assert found is not None, f"Expected '{name}' after position {idx}, got {names}"
            idx = found
        assert "Finish Turning" in names
        assert "Final Inspection" in names

    def test_camshaft_finish_turning_before_heat(self):
        req, geom = self._cam_request()
        route = build_route(req, geom, {})
        finish = next(op for op in route if op["name"] == "Finish Turning")
        ht = next(op for op in route if op["name"] == "Heat Treatment")
        assert finish["stage"] == ProcessStage.finish_before_heat.value
        assert finish["operation_no"] < ht["operation_no"]

    def test_camshaft_induction_hardening_skips_local_cam_induction(self):
        # The whole shaft is induction hardened -> no local cam induction hardening
        req, geom = self._cam_request(heat="induction_hardening")
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        assert "Heat Treatment" in names
        assert "Induction Harden Cam Lobe" not in names
        assert "CBN Finish Grind Cam Lobe" in names

    def test_cam_feature_covered(self):
        req, geom = self._cam_request()
        route = build_route(req, geom, {})
        cam_ops = [op for op in route if op.get("feature_id") == "F1"]
        assert len(cam_ops) >= 1
        assert any(op["process_category"] == "Cam Grinding" for op in cam_ops)

    def test_non_cam_route_unaffected(self):
        req = _make_request()
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        assert "CBN Finish Grind Cam Lobe" not in names


class TestCrankshaftRoute:
    """Crankshaft: quench and temper + CBN finish grinding of crank pins + fillet rolling + dynamic balancing."""

    def _crank_request(self):
        crank = {
            "feature_id": "F2",
            "feature_type": "crank_pin",
            "positioning_mode": "global_absolute",
            "global_position_mm": 50,
            "high_precision": True,
            "feature_length_mm": 25.0,
            "crank_pin_diameter_mm": 30.0,
            "crank_pin_width_mm": 25.0,
            "crank_offset_mm": 10.0,
        }
        req = _make_request(
            global_requirements={
                "heat_treatment": "quench_temper",
                "surface_treatment": "none",
                "target_hardness_hrc": 55,
                "batch_quantity": 1,
            },
            features=[crank],
        )
        geom = _make_geometry(req)
        geom["features"] = [crank]
        return req, geom

    def test_crankshaft_route_has_crank_production_chain(self):
        req, geom = self._crank_request()
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        expected_chain = [
            "Rough Turn Crank Pins",
            "Finish Turn Crank Pins",
            "Heat Treatment",
            "CBN Grind Crank Pins",
            "Fillet Rolling",
            "Dynamic Balancing",
            "Magnetic Particle Inspection",
        ]
        idx = -1
        for name in expected_chain:
            found = next((i for i, n in enumerate(names) if n == name and i > idx), None)
            assert found is not None, f"Expected '{name}' after position {idx}, got {names}"
            idx = found

    def test_crankshaft_finish_turning_before_heat(self):
        req, geom = self._crank_request()
        route = build_route(req, geom, {})
        finish = next(op for op in route if op["name"] == "Finish Turning")
        ht = next(op for op in route if op["name"] == "Heat Treatment")
        assert finish["stage"] == ProcessStage.finish_before_heat.value
        assert finish["operation_no"] < ht["operation_no"]

    def test_dynamic_balancing_unconditional(self):
        req, geom = self._crank_request()
        route = build_route(req, geom, {})
        db = next(op for op in route if op["name"] == "Dynamic Balancing")
        assert db["conditional"] is False

    def test_crank_pin_feature_covered(self):
        req, geom = self._crank_request()
        route = build_route(req, geom, {})
        crank_ops = [op for op in route if op.get("feature_id") == "F2"]
        categories = {op["process_category"] for op in crank_ops}
        assert "ISO Turning" in categories
        assert "Cylindrical Grinding" in categories

    def test_split_feature_gets_post_heat_finish(self):
        """A high-precision splittable feature on a crankshaft must be roughed pre-heat AND finished post-heat.

        Regression: the crankshaft builder scheduled split features before heat treatment but never
        appended the matching post-heat hard-finish operation, leaving the part oversized.
        """
        req, geom = self._crank_request()
        spline = {
            "feature_id": "F1",
            "feature_type": "spline",
            "positioning_mode": "global_absolute",
            "global_position_mm": 50,
            "high_precision": True,
            "feature_length_mm": 30.0,
            "spline_module": 1.5,
            "spline_teeth": 10,
        }
        req["features"] = [spline, geom["features"][0]]
        geom["features"] = [spline, geom["features"][0]]
        route = build_route(req, geom, {})
        spline_ops = [op for op in route if op.get("feature_id") == "F1"]
        stages = [op["stage"] for op in spline_ops]
        assert ProcessStage.feature_before_heat.value in stages, (
            f"missing pre-heat rough op: {spline_ops}"
        )
        assert ProcessStage.feature_after_heat.value in stages, (
            f"missing post-heat finish op: {spline_ops}"
        )


class TestWormShaftRoute:
    """Worm shaft: carburizing / nitriding / quench-temper three branches."""

    def _worm_request(self, heat):
        worm = {
            "feature_id": "F3",
            "feature_type": "worm",
            "positioning_mode": "global_absolute",
            "global_position_mm": 50,
            "high_precision": True,
            "feature_length_mm": 40.0,
            "worm_module": 2.0,
            "worm_starts": 1,
            "worm_pressure_angle_deg": 20.0,
            "worm_outer_diameter_mm": 35.0,
        }
        req = _make_request(
            global_requirements={
                "heat_treatment": heat,
                "surface_treatment": "none",
                "target_hardness_hrc": 58,
                "case_depth_mm": 1.0,
                "batch_quantity": 1,
            },
            features=[worm],
        )
        geom = _make_geometry(req)
        geom["features"] = [worm]
        return req, geom

    def test_carburized_worm_has_grinding_chain(self):
        req, geom = self._worm_request("carburize_quench")
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        expected_chain = [
            "Semi-finish Turn Spiral",
            "Pre-Clean",
            "Heat Treatment",
            "Lapping Center Holes",
            "Rough Grind Spiral",
            "Low-temp Aging",
            "Finish Grind Spiral",
        ]
        idx = -1
        for name in expected_chain:
            found = next((i for i, n in enumerate(names) if n == name and i > idx), None)
            assert found is not None, f"Expected '{name}' after position {idx}, got {names}"
            idx = found

    def test_nitrided_worm_rough_grind_before_heat(self):
        req, geom = self._worm_request("nitriding")
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        rough = next(i for i, n in enumerate(names) if n == "Rough Grind Spiral")
        ht = next(i for i, n in enumerate(names) if n == "Heat Treatment")
        finish = next(i for i, n in enumerate(names) if n == "Finish Grind Spiral")
        assert rough < ht < finish
        assert "Pre-Clean" not in names

    def test_worm_feature_covered(self):
        req, geom = self._worm_request("nitriding")
        route = build_route(req, geom, {})
        worm_ops = [op for op in route if op.get("feature_id") == "F3"]
        categories = {op["process_category"] for op in worm_ops}
        assert "ISO Turning" in categories
        assert "Worm Grinding" in categories

    def test_non_worm_route_unaffected(self):
        req = _make_request()
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        assert "Semi-finish Turn Spiral" not in names
        assert "Finish Grind Spiral" not in names

    def test_split_feature_gets_post_heat_finish(self):
        """A high-precision splittable feature on a carburized worm must be roughed pre-heat AND finished post-heat.

        Regression: the worm builder scheduled split features before heat treatment but never
        appended the matching post-heat hard-finish operation.
        """
        req, geom = self._worm_request("carburize_quench")
        spline = {
            "feature_id": "F1",
            "feature_type": "spline",
            "positioning_mode": "global_absolute",
            "global_position_mm": 50,
            "high_precision": True,
            "feature_length_mm": 30.0,
            "spline_module": 1.5,
            "spline_teeth": 10,
        }
        req["features"] = [spline, geom["features"][0]]
        geom["features"] = [spline, geom["features"][0]]
        route = build_route(req, geom, {})
        spline_ops = [op for op in route if op.get("feature_id") == "F1"]
        stages = [op["stage"] for op in spline_ops]
        assert ProcessStage.feature_before_heat.value in stages, (
            f"missing pre-heat rough op: {spline_ops}"
        )
        assert ProcessStage.feature_after_heat.value in stages, (
            f"missing post-heat finish op: {spline_ops}"
        )


class TestSurfaceHardenedShaftRoute:
    """Nitriding / induction hardening shafts: spindle, precision spline shaft, hollow shaft (only grinding is allowed after nitriding)."""

    def _shaft_request(self, heat, features=None):
        features = features or [
            {
                "feature_id": "F4",
                "feature_type": "spline",
                "positioning_mode": "global_absolute",
                "global_position_mm": 50,
                "high_precision": False,
                "feature_length_mm": 40.0,
                "spline_type": "involute",
                "spline_teeth": 20,
                "spline_module": 2.0,
            }
        ]
        req = _make_request(
            global_requirements={
                "heat_treatment": heat,
                "surface_treatment": "none",
                "target_hardness_hrc": 55,
                "batch_quantity": 1,
            },
            features=features,
        )
        geom = _make_geometry(req)
        geom["features"] = features
        return req, geom

    def test_nitrided_spindle_has_grinding_chain(self):
        req, geom = self._shaft_request("nitriding")
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        expected_chain = [
            "Finish Turning",
            "Heat Treatment",
            "Repair Center Holes",
            "Semi-finish Grind OD",
            "Finish Grind OD",
            "Lapping & Polishing",
            "Magnetic Particle / NDT Inspection",
        ]
        idx = -1
        for name in expected_chain:
            found = next((i for i, n in enumerate(names) if n == name and i > idx), None)
            assert found is not None, f"Expected '{name}' after position {idx}, got {names}"
            idx = found

    def test_nitriding_finish_turning_before_heat(self):
        req, geom = self._shaft_request("nitriding")
        route = build_route(req, geom, {})
        finish = next(op for op in route if op["name"] == "Finish Turning")
        ht = next(op for op in route if op["name"] == "Heat Treatment")
        assert finish["stage"] == ProcessStage.finish_before_heat.value
        assert finish["operation_no"] < ht["operation_no"]

    def test_induction_hardening_route(self):
        req, geom = self._shaft_request("induction_hardening")
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        assert "Heat Treatment" in names
        assert "Finish Grind OD" in names
        assert "Lapping & Polishing" in names

    def test_quench_temper_has_no_lapping(self):
        # A quench-temper shaft (no new features) follows the general route and must not contain lapping and polishing
        req, geom = self._shaft_request("quench_temper")
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        assert "Lapping & Polishing" not in names

    def test_hollow_nitrided_shaft_has_deep_hole_chain(self):
        bore = {
            "feature_id": "F5",
            "feature_type": "bore",
            "positioning_mode": "global_absolute",
            "global_position_mm": 0,
            "high_precision": False,
            "bore_diameter_mm": 10.0,
            "bore_length_mm": 100.0,
            "bore_through": True,
        }
        req = _make_request(
            blank_type="hollow",
            blank_inner_diameter_mm=10.0,
            global_requirements={
                "heat_treatment": "nitriding",
                "surface_treatment": "none",
                "target_hardness_hrc": 55,
                "batch_quantity": 1,
            },
            features=[bore],
        )
        geom = _make_geometry(req)
        geom["features"] = [bore]
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        assert "Deep Hole Drilling" in names  # L/D = 100/10 = 10 > 5
        assert "Rough Boring" in names
        assert "Finish Boring" in names


class TestKnowledgeBaseAlignment:
    """Knowledge-base-driven rule engine behavior (cnc_machining.md / grinding_process.md / turning_process.md)."""

    def _make_feature(self, feature_type, **fields):
        feature = {
            "feature_id": "F1",
            "feature_type": feature_type,
            "positioning_mode": "global_absolute",
            "global_position_mm": 30,
            "high_precision": False,
            "processing_timing": "undecided",
        }
        feature.update(fields)
        return feature

    def test_quenched_seal_area_uses_hard_turn(self):
        """Quench-split seal area uses hard turning instead of grinding (以车代磨)."""
        feature = self._make_feature(
            "seal_area",
            seal_type="rubber",
            seal_diameter_mm=28,
            feature_length_mm=25,
            roughness_ra=0.4,
            high_precision=True,
            processing_timing="before_and_after_heat_treatment",
        )
        req = _make_request(
            global_requirements={
                "heat_treatment": "quench_temper",
                "surface_treatment": "none",
                "batch_quantity": 1,
            },
            features=[feature],
        )
        geom = _make_geometry(req)
        geom["features"] = [feature]
        route = build_route(req, geom, {})
        feature_ops = [op for op in route if op.get("feature_id") == "F1"]
        assert [(op["name"], op.get("process_category")) for op in feature_ops] == [
            ("Rough turn seal area", "ISO Turning"),
            ("Finish hard turn seal area", "ISO Turning"),
        ]

    def test_unquenched_precision_seal_area_keeps_grinding(self):
        """A precision seal area without heat treatment keeps the finish-grind path."""
        feature = self._make_feature(
            "seal_area",
            seal_type="rubber",
            seal_diameter_mm=28,
            feature_length_mm=25,
            roughness_ra=0.4,
            high_precision=True,
        )
        req = _make_request(features=[feature])
        geom = _make_geometry(req)
        geom["features"] = [feature]
        route = build_route(req, geom, {})
        feature_ops = [op for op in route if op.get("feature_id") == "F1"]
        assert [(op["name"], op.get("process_category")) for op in feature_ops] == [
            ("Precision grind seal area", "Cylindrical Grinding"),
        ]

    def test_center_hole_lapping_precedes_finish_grind(self):
        """Grinding routes schedule center-hole lapping before the finish grind (grinding datum)."""
        req = _make_request(
            segments=[
                {
                    "segment_id": "S1",
                    "diameter_mm": 30,
                    "length_mm": 100,
                    "diameter_upper_deviation_mm": 0.005,
                    "diameter_lower_deviation_mm": -0.005,
                }
            ]
        )
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        names = [op["name"] for op in route]
        assert "Center Hole Lapping" in names
        assert names.index("Center Hole Lapping") < names.index("Finish Grind OD")

    def test_slender_shaft_straighten_note(self):
        """Slender shafts (L/D > 30) carry the deflection-control straightening note."""
        req = _make_request(
            blank_diameter_mm=20,
            segments=[
                {"segment_id": "S1", "diameter_mm": 18, "length_mm": 560, "roughness_ra": 3.2},
            ],
        )
        geom = _make_geometry(req)
        assert geom["total_length_mm"] / geom["max_finished_diameter_mm"] > 30
        route = build_route(req, geom, {})
        straighten = next(op for op in route if op["name"] == "Straighten")
        assert "Slender shaft (L/D>30)" in straighten["description"]

    def test_non_slender_shaft_standard_straighten_note(self):
        req = _make_request()
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        straighten = next(op for op in route if op["name"] == "Straighten")
        assert "Slender shaft" not in straighten["description"]

    def test_turning_allowance_in_descriptions(self):
        """Turning descriptions carry knowledge-base allowance values."""
        req = _make_request()
        geom = _make_geometry(req)
        route = build_route(req, geom, {})
        rough = next(op for op in route if op["name"] == "Rough Turning")
        semi = next(op for op in route if op["name"] == "Semi-finish Turning")
        assert "allowance ~" in rough["description"]
        assert "allowance ~" in semi["description"]
