"""Worker 优雅关闭与 trace_id 行为（单元层）。"""

from __future__ import annotations

import io
import json
import signal
import threading

from apps.worker.main import Worker
from src.core.logging import configure_logging

from tests.helpers import make_settings


def read_events(buffer: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


def run_worker_logged(idle_seconds: float, pre_set: bool, stop_after: float | None):
    """运行 worker 并收集结构化日志。pre_set=True 时启动前已请求关闭。"""
    settings = make_settings()
    buffer = io.StringIO()
    configure_logging(settings, output=buffer)
    shutdown = threading.Event()
    if pre_set:
        shutdown.set()
    if stop_after is not None:
        threading.Timer(stop_after, shutdown.set).start()
    worker = Worker(settings, idle_seconds=idle_seconds, shutdown=shutdown)
    worker.run()
    return read_events(buffer)


def test_worker_starts_and_stops_with_trace_id_in_every_event():
    events = run_worker_logged(idle_seconds=0.02, pre_set=False, stop_after=0.05)
    kinds = [event["event"] for event in events]
    assert kinds[0] == "worker_start"
    assert kinds[-1] == "worker_stop"
    for event in events:
        assert event.get("trace_id"), f"事件 {event['event']} 缺少 trace_id"


def test_worker_stops_accepting_new_work_after_shutdown_signal():
    """shutdown 置位后不再进入新循环：无 worker_cycle、正常退出。"""
    events = run_worker_logged(idle_seconds=5.0, pre_set=True, stop_after=None)
    kinds = [event["event"] for event in events]
    assert "worker_cycle" not in kinds  # 未接受任何新工作
    assert kinds == ["worker_start", "worker_stop"]


def test_worker_graceful_stop_completes_current_cycle():
    """运行中收到终止信号：完成当前循环后退出，且有 idle 心跳痕迹。"""
    events = run_worker_logged(idle_seconds=0.05, pre_set=False, stop_after=0.12)
    kinds = [event["event"] for event in events]
    assert "worker_cycle" in kinds  # 终止前至少完成一个执行单元
    assert kinds[-1] == "worker_stop"


def test_signal_handlers_installed_on_main_thread():
    settings = make_settings()
    worker = Worker(settings)
    worker.install_signal_handlers()
    assert signal.getsignal(signal.SIGTERM) == worker._handle_terminate
    assert signal.getsignal(signal.SIGINT) == worker._handle_terminate


def test_worker_startup_log_has_no_secrets():
    events = run_worker_logged(idle_seconds=0.02, pre_set=True, stop_after=None)
    startup = events[0]
    content = json.dumps(startup, ensure_ascii=False)
    assert "unit-test-password" not in content
    assert startup["capabilities"] == {"tushare": False, "llm": False}
    # 快速退出：启动即请求关闭时应立即返回
    assert startup["environment"] == "ci"
