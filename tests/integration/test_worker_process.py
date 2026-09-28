"""Worker 进程端到端：真实 SIGTERM 优雅关闭 + 日志脱敏（子进程级证据）。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
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

STARTUP_DEADLINE_SECONDS = 15
SHUTDOWN_DEADLINE_SECONDS = 15


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
    # 独立线程持续排空 stdout：避免管道写满死锁，也让主线程可以限时轮询
    lines: list[str] = []
    reader = threading.Thread(
        target=lambda: [lines.append(line) for line in proc.stdout], daemon=True
    )
    reader.start()
    try:
        # 1) 等待 worker_start 出现（限时，不依赖 readline 阻塞语义）
        deadline = time.monotonic() + STARTUP_DEADLINE_SECONDS
        while time.monotonic() < deadline:
            assert proc.poll() is None, (
                f"worker 提前退出: rc={proc.returncode}\n{''.join(lines)}"
            )
            if any('"worker_start"' in line for line in lines):
                break
            time.sleep(0.05)
        else:
            raise AssertionError(
                f"worker 未在 {STARTUP_DEADLINE_SECONDS}s 内输出 worker_start\n{''.join(lines)}"
            )

        # 2) 发送真实 SIGTERM，限时等待优雅退出
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=SHUTDOWN_DEADLINE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.wait(timeout=5)
            raise AssertionError("worker 未在期限内优雅退出（SIGTERM 后仍在运行）") from exc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    reader.join(timeout=2)
    full_output = "".join(lines)

    # 优雅关闭：正常退出码 + 生命周期日志齐全
    assert proc.returncode == 0, f"退出码异常: {proc.returncode}\n{full_output}"
    assert '"worker_start"' in full_output
    assert '"worker_stop"' in full_output
    # 日志脱敏：密码/Token 不得出现在进程输出中
    assert "worker-e2e-secret" not in full_output
