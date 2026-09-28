"""文件存储端口与本地实现（issue #6）。

不变量：
- 原始文件不可被解析结果覆盖：写入走临时文件 + 完整 Hash 验证后原子重命名；
- storage_key 不可预测（uuid 前缀），原始字节永不原地修改；
- 孤儿可枚举：list_keys 与数据库对账。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Protocol


class StorageError(Exception):
    """存储写入/读取失败。"""


class FileStorage(Protocol):
    def put(self, data: bytes, *, expected_sha256: str | None = None) -> str: ...
    def get(self, storage_key: str) -> bytes: ...
    def exists(self, storage_key: str) -> bool: ...
    def delete(self, storage_key: str) -> None: ...
    def list_keys(self) -> list[str]: ...


class LocalFileStorage:
    """本地卷实现：tempfile + fsync + 原子 rename。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._tmp_dir = self.root / ".tmp"
        self._tmp_dir.mkdir(exist_ok=True)

    def put(self, data: bytes, *, expected_sha256: str | None = None) -> str:
        digest = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise StorageError(
                f"hash mismatch on write: expected {expected_sha256[:12]}, got {digest[:12]}"
            )
        storage_key = f"{uuid.uuid4().hex}/{digest}"
        target = self.root / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._tmp_dir / f"{uuid.uuid4().hex}.part"
        try:
            with open(temp_path, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        except OSError as exc:
            temp_path.unlink(missing_ok=True)
            raise StorageError(f"atomic write failed: {exc}") from exc
        return storage_key

    def get(self, storage_key: str) -> bytes:
        path = self.root / storage_key
        if not path.is_file():
            raise StorageError(f"missing object: {storage_key}")
        return path.read_bytes()

    def exists(self, storage_key: str) -> bool:
        return (self.root / storage_key).is_file()

    def delete(self, storage_key: str) -> None:
        (self.root / storage_key).unlink(missing_ok=True)

    def list_keys(self) -> list[str]:
        keys = [
            str(path.relative_to(self.root))
            for path in self.root.rglob("*")
            if path.is_file() and ".tmp" not in path.parts
        ]
        return sorted(keys)

    def cleanup_temp(self) -> int:
        """清理残留半文件（崩溃恢复）。"""
        removed = 0
        for part in self._tmp_dir.glob("*.part"):
            part.unlink(missing_ok=True)
            removed += 1
        return removed


def copy_fixture_to_storage(source: Path, storage: LocalFileStorage) -> tuple[str, bytes]:
    """Fixture 辅助：读文件 → 写存储，返回 (storage_key, 原始字节)。"""
    data = source.read_bytes()
    key = storage.put(data)
    return key, data
