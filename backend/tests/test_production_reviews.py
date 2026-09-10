# 回归测试：覆盖制造几何反例、独立审查证据、修复和安全定制。
"""Manufacturing counterexamples and model-isolated multi-agent integration tests."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from models.workflow import PlanningRequest
from models.process import ProcessOperation
from rules.geometry import dimension_route_errors
from workflow import Workflow, JobStore
from service import PlanningService
from agents.specialists import SpecialistAgent, coordinate_reviews, route_fingerprint


# 构造制造审查使用的基础合法请求，允许按反例覆盖字段。
def request(**changes):
    data = dict(
        material="45",
        blank_diameter_mm=50,
        segments=[dict(segment_id="S1", diameter_mm=30, length_mm=100)],
        features=[],
        global_requirements=dict(heat_treatment="none"),
    )
    data.update(changes)
    return PlanningRequest(**data)


# 运行规则工作流并保存结果，供审查及定制回归测试复用。
def workflow_state(req=None):
    store = JobStore()
    payload = (req or request()).model_dump()
    store.create("review-test", payload)
    flow = Workflow(store)
    state = flow.graph.invoke(
        dict(job_id="review-test", request=payload), {"configurable": {"thread_id": "review-test"}}
    )
    store.update("review-test", status=state["status"], result=state)
    return flow, store, state


# 验证明确不热处理时不添加热处理，也不触发无关人工选择。
def test_explicit_none_is_preserved_and_does_not_interrupt():
    req = request(
        features=[
            dict(
                feature_id="F1",
                feature_type="keyway",
                positioning_mode="global_absolute",
                global_position_mm=10,
                keyway_width_mm=8,
                keyway_depth_mm=3,
                feature_length_mm=20,
                roughness_ra=0.4,
            )
        ]
    )
    assert req.global_requirements.heat_treatment == "none"
    _, _, state = workflow_state(req)
    assert state["status"] == "completed"
    assert not state.get("__interrupt__")
    assert not any(op["name"] == "Heat Treatment" for op in state["process_route"])


# 验证公差颠倒、孔越界和毛坯包络等制造几何反例被拒绝。
@pytest.mark.parametrize(
    "changes",
    [
        dict(
            segments=[
                dict(
                    segment_id="S1",
                    diameter_mm=30,
                    length_mm=100,
                    diameter_upper_deviation_mm=-0.1,
                    diameter_lower_deviation_mm=0.1,
                )
            ]
        ),
        dict(blank_type="hollow", blank_inner_diameter_mm=35),
        dict(
            features=[
                dict(
                    feature_id="B",
                    feature_type="bore",
                    positioning_mode="global_absolute",
                    global_position_mm=90,
                    bore_diameter_mm=10,
                    bore_length_mm=50,
                )
            ]
        ),
        dict(
            features=[
                dict(
                    feature_id="F",
                    feature_type="flange",
                    positioning_mode="global_absolute",
                    global_position_mm=10,
                    flange_diameter_mm=80,
                    flange_thickness_mm=10,
                )
            ]
        ),
        dict(
            blank_type="hollow",
            blank_inner_diameter_mm=20,
            features=[
                dict(
                    feature_id="B",
                    feature_type="bore",
                    positioning_mode="global_absolute",
                    global_position_mm=0,
                    bore_diameter_mm=19,
                    bore_length_mm=50,
                )
            ],
        ),
        dict(blank_diameter_mm=float("inf")),
    ],
)
def test_invalid_manufacturing_geometry_rejected(changes):
    with pytest.raises(ValidationError):
        request(**changes)


# 验证毛坯内孔信息不会虚构成品孔目标或切削工序。
def test_stock_bore_does_not_create_fictional_cutting():
    _, _, state = workflow_state(request(blank_type="hollow", blank_inner_diameter_mm=20))
    assert not any(
        op["name"] in ("Rough Boring", "Finish Boring", "Deep Hole Drilling")
        for op in state["process_route"]
    )
    assert any(
        f["code"] == "FINISHED_BORE_UNSPECIFIED" for f in state["agent_collaboration"]["findings"]
    )


# 验证三个专家绑定同一路线且结果不自动获得生产放行。
def test_all_specialists_review_same_route_and_never_release():
    _, _, state = workflow_state()
    council = state["agent_collaboration"]
    assert len(council["reports"]) == 3
    assert not council["production_release"]
    assert council["route_fingerprint"] == route_fingerprint(state["process_route"])
    assert {r["mode"] for r in council["reports"]} == {"rules_only", "not_applicable"}


# 模拟模型请求实际工具并引用返回证据，验证审查工具链。
def test_model_requests_real_tool_and_uses_evidence(monkeypatch):
    flow, _, state = workflow_state()
    import agents.specialists as specialists

    monkeypatch.setattr(specialists, "llm_available", lambda: True)
    responses = iter(
        [
            {"tools": [{"name": "inspect_route"}]},
            {
                "findings": [
                    dict(
                        code="DATUM_CHECK",
                        message="Confirm the clamping datum.",
                        evidence_ids=["tool_1"],
                        recommendation="Review fixture drawing.",
                    )
                ],
                "summary": "Reviewed with route inspection evidence.",
            },
        ]
    )
    monkeypatch.setattr(specialists, "chat_json", lambda *a, **kw: next(responses))
    report = (
        SpecialistAgent(flow, "machining_review").execute(state).state_updates["machining_review"]
    )
    assert report["mode"] == "model_review"
    assert report["tool_calls"][0]["tool"] == "inspect_route"
    assert report["evidence"]["tool_1"]["route"] == state["process_route"]
    assert any(f["source"] == "model_proposal" for f in report["findings"])


# 验证模型输出无效时保留确定性发现，并标记审查降级。
@pytest.mark.parametrize(
    "response",
    [
        {"tools": [{"name": "execute_shell"}]},
        {
            "findings": [
                dict(
                    code="UNSUPPORTED",
                    message="Bad",
                    evidence_ids=["invented"],
                    recommendation="Bad",
                )
            ]
        },
    ],
)
def test_invalid_model_output_degrades_without_losing_rules(monkeypatch, response):
    flow, _, state = workflow_state()
    import agents.specialists as specialists

    monkeypatch.setattr(specialists, "llm_available", lambda: True)
    monkeypatch.setattr(specialists, "chat_json", lambda *a, **kw: response)
    report = SpecialistAgent(flow, "quality_review").execute(state).state_updates["quality_review"]
    assert report["mode"] == "degraded"
    assert report["findings"] and all(f["source"] == "deterministic" for f in report["findings"])


# 模拟持续工具请求，验证专家预算能够终止模型循环。
def test_tool_budget_terminates_agent(monkeypatch):
    flow, _, state = workflow_state()
    import agents.specialists as specialists

    monkeypatch.setattr(specialists, "llm_available", lambda: True)
    calls = []

    # 生成重复工具请求的模拟模型响应，用于验证预算边界。
    def response(*a, **kw):
        calls.append(1)
        return {"tools": [{"name": "inspect_route"}]}

    monkeypatch.setattr(specialists, "chat_json", response)
    report = SpecialistAgent(flow, "quality_review").execute(state).state_updates["quality_review"]
    assert len(calls) == 3 and report["mode"] == "degraded"


# 修改路线后验证旧审查报告不能用于新版本。
def test_stale_reports_cannot_be_reused():
    _, _, state = workflow_state()
    state["process_route"][0]["description"] += " changed"
    council = coordinate_reviews(state)["agent_collaboration"]
    assert len(council["degraded"]) == 3
    assert not council["production_release"]


# 验证删除必需工序的定制被拒绝，线上任务快照保持原样。
def test_custom_route_rejects_deletion_without_mutating_live_job():
    flow, store, state = workflow_state()
    svc = object.__new__(PlanningService)
    svc.store, svc.workflow = store, flow
    before = store.get("review-test")
    final = next(op for op in state["process_route"] if op["name"] == "Final Inspection")
    with pytest.raises(ValueError, match="revalidation"):
        svc.customize_route("review-test", [ProcessOperation(**final)])
    assert store.get("review-test") == before


# 验证合法编辑重新匹配和审查，并递增路线修订号。
def test_valid_edit_has_fresh_resources_reviews_and_revision():
    flow, store, state = workflow_state()
    svc = object.__new__(PlanningService)
    svc.store, svc.workflow = store, flow
    edited = deepcopy(state["process_route"])
    edited[0]["description"] = "Cut stock according to approved purchase specification."
    svc.customize_route("review-test", [ProcessOperation(**op) for op in edited])
    result = svc.result("review-test")
    assert result["route_revision"] == 1
    assert result["process_route"][0]["description"] == edited[0]["description"]
    assert result["agent_collaboration"]["route_fingerprint"] == route_fingerprint(
        result["process_route"]
    )
    assert len(result["resource_selection"]["operation_resources"]) == len(edited)
    svc.reset_custom_route("review-test")
    assert svc.result("review-test")["process_route"] == state["process_route"]
    assert svc.result("review-test")["route_revision"] == 2


# 验证内孔直径不能在后续去料工序中缩小。
def test_internal_diameter_cannot_shrink_across_operations():
    route = [
        dict(
            operation_no=1,
            name="Bore",
            stage="rough",
            dimensions=[dict(object_id="B", surface="internal", before_mm=20, after_mm=21)],
        ),
        dict(
            operation_no=2,
            name="Finish Bore",
            stage="finish",
            dimensions=[dict(object_id="B", surface="internal", after_mm=20)],
        ),
    ]
    assert dimension_route_errors(route)


# 强制首次审查修复，验证修复标记不会被基础路线再生成覆盖。
def test_repair_route_is_not_overwritten_by_base_planning(monkeypatch):
    # Force a first-review error, then accept the repaired route. The original
    # bug regenerated the base route between repair and the next verification.
    original = SpecialistAgent.execute

    # 在首次质量审查注入可修复问题，供路线修复回归测试使用。
    def review(self, state):
        result = original(self, state)
        if self.name == "quality_review" and not state.get("repair_count"):
            result.state_updates[self.name]["findings"].append(
                dict(
                    code="REPAIR_MARKER",
                    message="Add clarification",
                    severity="error",
                    disposition="route_repair",
                    operation_nos=[1],
                    evidence_ids=["route"],
                    recommendation="Clarify first operation",
                    source="model_proposal",
                )
            )
        return result

    monkeypatch.setattr(SpecialistAgent, "execute", review)
    from workflow.nodes.verification import VerificationNodesMixin

    # 给首道工序添加可识别修复标记并递增次数，检测后续是否覆盖修复。
    def repair(self, state):
        route = deepcopy(state["process_route"])
        route[0]["description"] += " REPAIRED"
        return {"process_route": route, "repair_count": state.get("repair_count", 0) + 1}

    monkeypatch.setattr(VerificationNodesMixin, "repair", repair)
    _, _, state = workflow_state()
    assert state["status"] == "completed"
    assert state["repair_count"] == 1
    assert state["process_route"][0]["description"].endswith("REPAIRED")
    assert not state["agent_collaboration"]["repair_requests"]


# 使用线程屏障验证三个领域专家确实并行执行。
def test_specialists_are_scheduled_in_parallel(monkeypatch):
    import threading

    barrier = threading.Barrier(3)
    original = SpecialistAgent.execute
    threads = set()

    # 记录线程并等待审查屏障，用于证明专家在不同线程并发执行。
    def synchronized(self, state):
        threads.add(threading.get_ident())
        barrier.wait(timeout=5)
        return original(self, state)

    monkeypatch.setattr(SpecialistAgent, "execute", synchronized)
    _, _, state = workflow_state()
    assert len(threads) == 3
    assert state["status"] == "completed"


# 验证智能体缺少声明输出时不能返回成功。
def test_missing_agent_output_fails_closed():
    from agents.base import BaseAgent, AgentCapability, AgentResult

    # 故意不返回必需字段的测试智能体，用于验证失败关闭行为。
    class BrokenAgent(BaseAgent):
        # 声明该智能体读取和产出的状态键，供执行前后检查。
        def capabilities(self):
            return AgentCapability(
                name=self.name, description="test", produces_state_keys=["required_report"]
            )

        # 故意返回缺少必需状态字段的成功标志，检测输出校验能否纠正它。
        def execute(self, state):
            return AgentResult(success=True, state_updates={})

    result = BrokenAgent("broken").safe_execute({})
    assert not result.success and "required_report" in result.error


# 验证空心件要求装夹确认，而不是向已有内孔安排中心孔钻削。
def test_hollow_shaft_requires_workholding_instead_of_center_drilling():
    _, _, state = workflow_state(request(blank_type="hollow", blank_inner_diameter_mm=20))
    names = {op["name"] for op in state["process_route"]}
    assert "Prepare Workholding" in names and "Center Drilling" not in names
    assert state["verification"]["checks"][0]["passed"]
