# 回归测试：覆盖任务状态跨存储实例的持久化。
from workflow.job_store import JobStore


# 重新创建存储实例，验证已保存任务仍能读取。
def test_job_store_persists_across_instances(tmp_path, monkeypatch):
    database = tmp_path / "jobs.sqlite3"
    monkeypatch.setenv("JOB_DB_FILE", str(database))
    first = JobStore()
    first.create("job-1", {"material": "45"})
    first.update("job-1", status="completed", result={"route": [1]})

    second = JobStore()
    restored = second.get("job-1")
    assert restored["status"] == "completed"
    assert restored["result"] == {"route": [1]}
    assert database.stat().st_mode & 0o777 == 0o600
