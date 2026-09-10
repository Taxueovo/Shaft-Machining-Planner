# 回归测试：覆盖本地令牌鉴权、请求体上限和响应信息暴露。
"""Security boundary checks for the loopback backend API."""

from fastapi.testclient import TestClient

from app import LOCAL_API_TOKEN, app
from repositories import MACHINE_FILE, TOOL_FILE


# 验证特权接口必须提供正确本地令牌，未授权调用被拒绝。
def test_privileged_api_requires_local_token() -> None:
    with TestClient(app) as client:
        denied = client.post("/api/v1/heartbeat")
        allowed = client.post(
            "/api/v1/heartbeat",
            headers={"x-local-api-token": LOCAL_API_TOKEN},
        )

    assert denied.status_code == 401
    assert denied.headers["cache-control"] == "no-store"
    assert allowed.status_code == 200


# 验证超大请求在正文解析前被拒绝，避免无界内存消耗。
def test_oversized_api_request_is_rejected_before_parsing() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/jobs",
            content=b"{}",
            headers={
                "content-length": "2000001",
                "content-type": "application/json",
                "x-local-api-token": LOCAL_API_TOKEN,
            },
        )

    assert response.status_code == 413


# 验证健康响应不包含本地目录或配置路径。
def test_health_response_does_not_disclose_local_paths() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    text = response.text
    assert str(MACHINE_FILE) not in text
    assert str(TOOL_FILE) not in text
