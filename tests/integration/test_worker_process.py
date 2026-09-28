"""Worker 进程端到端：真实 SIGTERM 优雅关闭 + 日志脱敏（子进程级证据）。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

ENV_VARS = {
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_DB": "ontology",
    "POSTGRES_USER": "ontology",
    "POSTGRES_PASSWORD": "worker-e2e-secret",
    "NEO4J_URI": "bolt://127.0.0.1:7687",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "worker-e2e-secret",
    "ENVIRONMENT": "ci",
    "LOG_LEVEL": "INFO",
    "TUSHARE_TOKEN": "",
    "LLM_API_KEY": "",
}


def test_worker_process_graceful_shutdown_on_sigterm():
    env = os.environ.copy()
    env.update(ENV_VARS)
    proc = subprocess.Popen(
        [sys.executable, "-m", "apps.worker.main"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        # 等待 worker_start 出现（最长 15s，覆盖首次导入）
        deadline = time.monotonic() + 15
        output = ""
        while time.monotonic() < deadline:
            assert proc.poll() is None, f"worker 提前退出: {proc.returncode}"
            output = proc.stdout.readline() if proc.stdout else ""
            # 逐行读到 start 即可；其余留给 communicate
            if '"worker_start"' in output:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("worker 未在期限内输出 worker_start")

        proc.send_signal(signal.SIGTERM)
        rest, _ = proc.communicate(timeout=15)
        full_output = output + (rest or "")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    # 优雅关闭：正常退出码 + 生命周期日志齐全
    assert proc.returncode == 0, f"退出码异常: {proc.returncode}\n{full_output}"
    assert '"worker_start"' in full_output
    assert '"worker_stop"' in full_output
    # 日志脱敏：密码/Token 不得出现在进程输出中
    assert "worker-e2e-secret" not in full_output
