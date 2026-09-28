"""SemanticRuntime 空端口（ADR-0003）。

语义运行时协议占位：由后续工作包（SemanticaRuntimeAdapter）填充方法签名。
业务代码只允许依赖本端口，不得导入 Semantica 内部模块。
"""

from __future__ import annotations

from typing import Protocol


class SemanticRuntime(Protocol):
    """可替换语义运行时端口；方法在实现 Semantica 适配器的 issue 中定义。"""

    # 空端口：issue #1 阶段不引入任何具体能力签名。
