"""Worker 进程：基线阶段无业务工作，只建立可观测的空转循环。

不变量（issue #1）：
- 收到 SIGTERM/SIGINT 后停止接受新工作，完成当前循环后退出（优雅关闭）；
- 重启不依赖内存状态：全部配置来自环境变量；
- 每个工作循环绑定独立 trace_id，日志经脱敏处理器输出。
"""

from __future__ import annotations

import signal
import threading

import structlog
from src.core.bootstrap import load_settings_or_fail
from src.core.config import Settings
from src.core.logging import configure_logging
from src.core.trace import new_trace_id, reset_trace_id, set_trace_id

log = structlog.get_logger(__name__)


class Worker:
    def __init__(
        self,
        settings: Settings,
        *,
        idle_seconds: float = 5.0,
        shutdown: threading.Event | None = None,
    ) -> None:
        self.settings = settings
        self.idle_seconds = idle_seconds
        self.shutdown = shutdown if shutdown is not None else threading.Event()

    def install_signal_handlers(self) -> None:
        """SIGTERM/SIGINT → 设置 shutdown 事件；信号处理必须在主线程安装。"""
        signal.signal(signal.SIGTERM, self._handle_terminate)
        signal.signal(signal.SIGINT, self._handle_terminate)

    def _handle_terminate(self, signum: int, frame: object) -> None:
        self.shutdown.set()

    def run(self) -> None:
        token = set_trace_id(new_trace_id())
        try:
            log.info("worker_start", **self.settings.safe_summary())
        finally:
            reset_trace_id(token)

        while not self.shutdown.is_set():
            # 每个执行单元一个 trace_id；shutdown 事件置位后不再进入新循环
            token = set_trace_id(new_trace_id())
            try:
                log.info("worker_cycle", note="baseline idle cycle; no business work configured")
                self.shutdown.wait(timeout=self.idle_seconds)
            finally:
                reset_trace_id(token)

        token = set_trace_id(new_trace_id())
        try:
            log.info("worker_stop", detail="shutdown signal received; no new work accepted")
        finally:
            reset_trace_id(token)


def main() -> None:
    settings = load_settings_or_fail()  # 必填缺失时快速失败（脱敏 stderr + 退出码 2）
    configure_logging(settings)
    worker = Worker(settings)
    worker.install_signal_handlers()
    worker.run()


if __name__ == "__main__":
    main()
