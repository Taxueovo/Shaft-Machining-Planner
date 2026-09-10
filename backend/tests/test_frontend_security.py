# 回归测试：覆盖跨站写请求拦截和错误响应安全头。
"""Browser-facing loopback proxy security checks."""

from fastapi.testclient import TestClient

from frontend.main import app


# 验证跨站写请求在进入后端代理前即被拦截。
def test_cross_site_mutation_is_rejected_before_proxying() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/preview",
            json={},
            headers={"origin": "https://attacker.example", "sec-fetch-site": "cross-site"},
        )

    assert response.status_code == 403


# 验证错误响应同样携带浏览器安全头。
def test_browser_security_headers_cover_error_responses() -> None:
    with TestClient(app) as client:
        response = client.get("/does-not-exist")

    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "script-src 'self' 'unsafe-inline'" not in response.headers["content-security-policy"]
    assert "script-src 'self' 'nonce-" in response.headers["content-security-policy"]
