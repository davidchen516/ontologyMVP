"""uvicorn 入口（目标：apps.api.main:app）。

生产语义：导入即加载配置，必填项缺失时进程启动快速失败。
测试请直接使用 apps.api.app.create_app 构造应用。
"""

from __future__ import annotations

from src.core.config import Settings

from apps.api.app import create_app

settings = Settings()
app = create_app(settings)
