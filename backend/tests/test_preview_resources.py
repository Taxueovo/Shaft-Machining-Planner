# 回归测试：覆盖预览路线与公开机床库的资源匹配。
"""Tests for local resource capability checks in live route preview."""

from app import preview_route
from repositories import MachineRepository


# 验证预览结果包含实际本地资源匹配，而不是仅有路线文字。
def test_preview_route_returns_local_resource_matching():
    result = preview_route(
        {
            "material": "45",
            "blank_diameter_mm": 65,
            "segments": [{"segment_id": "S01", "diameter_mm": 50, "length_mm": 180}],
            "features": [],
            "global_requirements": {
                "heat_treatment": "none",
                "surface_treatment": "none",
                "batch_quantity": 1,
            },
        }
    )

    assert result["route"]
    assert result["capability"]["machine"]["required_length_mm"] == 180
    assert "ISO Turning" in result["capability"]["tool_checks"]

    operation_resources = result["resource_selection"]["operation_resources"]
    assert len(operation_resources) == len(result["route"])
    turning_operations = [
        item for item in operation_resources if item["process_category"] == "ISO Turning"
    ]
    assert turning_operations
    assert all(
        item["verification_status"] in {"satisfied", "not_covered", "unknown"}
        for item in turning_operations
    )


# 验证高精度轴承位预览保留明确的不热处理要求。
def test_preview_preserves_no_heat_for_precision_bearing_seat():
    result = preview_route(
        {
            "material": "45",
            "blank_diameter_mm": 35,
            "segments": [{"segment_id": "S01", "diameter_mm": 30, "length_mm": 100}],
            "features": [
                {
                    "feature_id": "F01",
                    "feature_type": "bearing_seat",
                    "positioning_mode": "global_absolute",
                    "global_position_mm": 20,
                    "bearing_seat_diameter_mm": 30,
                    "bearing_seat_tolerance": "IT6",
                    "feature_length_mm": 30,
                }
            ],
        }
    )

    names = [operation["name"] for operation in result["route"]]
    assert "Heat Treatment" not in names
    assert result["heat_treatment_decision"]["process_name"] is None
    assert any(op.get("feature_id") == "F01" for op in result["route"])


# 验证磨削工序查询对应磨床能力记录。
def test_grinding_routes_query_local_grinding_machine_records():
    result = preview_route(
        {
            "material": "45",
            "blank_diameter_mm": 35,
            "segments": [
                {
                    "segment_id": "S01",
                    "diameter_mm": 30,
                    "length_mm": 100,
                    "diameter_upper_deviation_mm": 0.005,
                    "diameter_lower_deviation_mm": -0.005,
                }
            ],
        }
    )

    grinding_operation = next(
        operation
        for operation in result["resource_selection"]["operation_resources"]
        if operation["process_category"] == "Cylindrical Grinding"
    )
    assert grinding_operation["machine_recommendations"]
    assert any(
        machine["unique_identifier"] == "PUBLIC-DMGMORI-NVG-7LH"
        for machine in grinding_operation["machine_recommendations"]
    )


# 验证齿轮磨削查询能够匹配公开磨齿设备记录。
def test_machine_repository_matches_new_gear_grinding_records():
    matches = MachineRepository().search_process("Gear Grinding", 450, 250)

    assert matches["conclusion"] == "satisfied"
    assert any(
        machine["unique_identifier"] == "PUBLIC-KAPPNILES-KNG3P"
        for machine in matches["active_matches"]
    )


# 验证滚齿查询覆盖扩充后的公开设备记录。
def test_machine_repository_matches_expanded_gear_hobbing_library():
    matches = MachineRepository().search_process("Gear Hobbing", 150, 50)

    assert matches["conclusion"] == "satisfied"
    assert {machine["unique_identifier"] for machine in matches["active_matches"]} >= {
        "PUBLIC-GLEASON-100H",
        "PUBLIC-GLEASON-PSERIES-MIDSIZE",
    }


# 验证模数约束参与筛选，未验证精度不能标为已确认。
def test_machine_repository_enforces_module_and_labels_unverified_precision():
    matches = MachineRepository().search_process(
        "Gear Hobbing", 150, 50, required_module=8, high_precision_required=True
    )

    identifiers = {machine["unique_identifier"] for machine in matches["active_matches"]}
    assert "PUBLIC-GLEASON-100H" not in identifiers
    assert "PUBLIC-GLEASON-PSERIES-MIDSIZE" in identifiers
    selected = next(
        machine
        for machine in matches["active_matches"]
        if machine["unique_identifier"] == "PUBLIC-GLEASON-PSERIES-MIDSIZE"
    )
    assert selected["confidence"] == "partial_public_limits"
    assert "required tolerance/accuracy grade" in selected["unverified_constraints"]
