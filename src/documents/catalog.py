"""文档源目录适配器（issue #6 BLOCKER-2 / 验收 1）。

- TushareAnnsCatalog：读取 #3 能力矩阵中 anns_d 的可用性；AVAILABLE 时
  作为公告目录（列表函数注入，Fixture 驱动，CI 零真实外呼）；
- OfficialWebCatalog：巨潮/上交所/深交所官方直接目录路径（列表函数注入）；
- select_catalog：anns_d 不可用（非 AVAILABLE）→ 官方渠道；
  来源切换必须显式记录并暴露数据新鲜度：
  - 切换记录写入 ops.source_capability（api_name=DOCUMENT_CATALOG），
    经 /admin/capabilities 可查询；
  - /admin/data-freshness 暴露文档侧最近下载时间与目录选择时间。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

CATALOG_API_NAME = "DOCUMENT_CATALOG"


class CatalogEntry:
    """目录条目：官方 ID / 标题 / URL / 内容 Hash（可选）。"""

    __slots__ = ("external_id", "title", "url", "content_hash", "published_at")

    def __init__(
        self,
        *,
        external_id: str,
        title: str,
        url: str,
        content_hash: str | None = None,
        published_at: dt.datetime | None = None,
    ) -> None:
        self.external_id = external_id
        self.title = title
        self.url = url
        self.content_hash = content_hash
        self.published_at = published_at


class CatalogSource(Protocol):
    name: str

    def available(self, uow: Any) -> bool: ...

    def list_documents(self, uow: Any, *, company_key: str | None = None) -> list[CatalogEntry]: ...


class TushareAnnsCatalog:
    """TuShare anns_d 目录：以 #3 能力矩阵（ops.source_capability）为准。"""

    name = "TUSHARE_ANNS"

    def __init__(self, list_fn: Callable[..., list[dict[str, Any]]]) -> None:
        # list_fn(external_id=None) 返回目录行：external_id/title/url/content_hash
        self._list_fn = list_fn

    def available(self, uow: Any) -> bool:
        capability = uow.source_capabilities.get_status("TUSHARE", "anns_d")
        return capability is not None and capability["status"] == "AVAILABLE"

    def list_documents(self, uow: Any, *, company_key: str | None = None) -> list[CatalogEntry]:
        rows = self._list_fn(company_key=company_key)
        return [
            CatalogEntry(
                external_id=str(row["external_id"]),
                title=str(row["title"]),
                url=str(row["url"]),
                content_hash=row.get("content_hash"),
                published_at=row.get("published_at"),
            )
            for row in rows
        ]


class OfficialWebCatalog:
    """官方直接目录（巨潮/上交所/深交所）：列表函数注入（Fixture/爬取器）。"""

    name = "OFFICIAL_WEB"

    def __init__(self, list_fn: Callable[..., list[dict[str, Any]]]) -> None:
        self._list_fn = list_fn

    def available(self, uow: Any) -> bool:
        return True  # 官方渠道按设计始终可用（降级终点）

    def list_documents(self, uow: Any, *, company_key: str | None = None) -> list[CatalogEntry]:
        rows = self._list_fn(company_key=company_key)
        return [
            CatalogEntry(
                external_id=str(row["external_id"]),
                title=str(row["title"]),
                url=str(row["url"]),
                content_hash=row.get("content_hash"),
                published_at=row.get("published_at"),
            )
            for row in rows
        ]


def select_catalog(
    uow: Any,
    *,
    tushare: TushareAnnsCatalog,
    official: OfficialWebCatalog,
) -> tuple[CatalogSource, dict[str, Any]]:
    """目录选择：TuShare 优先，非 AVAILABLE 显式降级到官方渠道。

    切换/选择必须记录（不静默）：写 ops.source_capability（DOCUMENT_CATALOG），
    detail 含 selected_source/fallback_from/checked_at，供 /admin/capabilities
    与新鲜度查询消费。
    """
    switched = False
    if tushare.available(uow):
        selected: CatalogSource = tushare
    else:
        selected = official
        switched = True

    detail = {
        "selected_source": selected.name,
        "fallback_from": "TUSHARE_ANNS" if switched else None,
        "checked_at": dt.datetime.now(tz=dt.UTC).isoformat(),
    }
    uow.source_capabilities.upsert(
        source_system="DOCUMENT_CATALOG",
        api_name=CATALOG_API_NAME,
        status="AVAILABLE",
        detail=detail,
    )
    if switched:
        log.warning(
            "document_catalog_switched",
            from_source="TUSHARE_ANNS", to_source=selected.name,
            reason="anns_d not AVAILABLE per capability matrix",
        )
    return selected, detail


def discover_from_catalog(
    uow: Any,
    catalog: CatalogSource,
    *,
    company_key: str | None = None,
) -> list[dict[str, Any]]:
    """把目录条目批量送入 discover_document（幂等）。"""
    import hashlib

    from src.documents.pipeline import discover_document

    results = []
    for entry in catalog.list_documents(uow, company_key=company_key):
        # 目录无内容 Hash 时使用 (官方ID|URL) 的临时身份——下载后的 file_hash
        # 才是权威内容指纹；空串会在 UNIQUE(document_id, content_hash) 上碰撞
        provisional_hash = entry.content_hash or hashlib.sha256(
            f"{entry.external_id}|{entry.url}".encode()
        ).hexdigest()
        results.append(
            discover_document(
                uow.document_versions,
                source_system=catalog.name,
                external_id=entry.external_id,
                title=entry.title,
                content_hash=provisional_hash,
                source_url=entry.url,
                source_note=f"catalog:{catalog.name}",
            )
        )
    return results
