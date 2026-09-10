# 回归测试：覆盖本地模型地址限制和纯规则模式。
import llm_client


# 验证本地模型模式只允许回环地址。
def test_local_model_url_accepts_loopback_only():
    assert llm_client._is_loopback_url("http://127.0.0.1:11434/v1")
    assert llm_client._is_loopback_url("http://[::1]:11434/v1")
    assert not llm_client._is_loopback_url("https://models.example.com/v1")
    assert not llm_client._is_loopback_url("file:///tmp/model")


# 验证规则模式不会启用模型调用。
def test_rules_provider_disables_llm(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "rules")
    assert llm_client.llm_available() is False
