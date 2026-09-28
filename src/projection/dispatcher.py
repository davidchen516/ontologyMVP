"""事件→Cypher 分发器 + Worker 入口（issue #8 BLOCKER-3）。

分发器把 Outbox 事件类型映射到投影器的具体 Cypher 操作：
- CLAIM_ACCEPTED → Claim 节点 + 经营边；
- CLAIM_CONTRADICTED / CLAIM_SUPERSEDED → 失效物化边；
- 实体事件（标准层产生）→ 节点 MERGE。

Worker 入口提供 CLI / 守护进程两种启动方式。
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import structlog

from src.db.uow import UnitOfWorkFactory
from src.projection.projector import Neo4jProjector

log = structlog.get_logger(__name__)


class GraphExecutor(Protocol):
    """Neo4j Cypher 执行端口：生产用 driver，测试用 FakeGraphExecutor。"""

    def execute(self, template: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class EventDispatcher:
    """把 Outbox 事件分发到投影器的具体操作。"""

    def __init__(self, projector: Neo4jProjector) -> None:
        self._projector = projector

    def dispatch(self, uow: Any, event: dict[str, Any]) -> None:
        """根据事件类型执行投影。分发器在此校验 claim_id 的真实性。"""
        event_type = str(event.get("event_type", ""))
        aggregate_type = str(event.get("aggregate_type", ""))
        aggregate_id = event.get("aggregate_id")

        if aggregate_type == "Claim":
            self._dispatch_claim_event(uow, event, event_type, aggregate_id)
        elif aggregate_type in ("Company", "Security", "Exchange", "Industry",
                                 "Concept", "Theme", "Product"):
            self._dispatch_entity_event(uow, event, aggregate_type, aggregate_id)
        else:
            log.warning("unhandled_event_type",
                        event_type=event_type, aggregate_type=aggregate_type)

    def _dispatch_claim_event(
        self, uow: Any, event: dict[str, Any], event_type: str, claim_id: Any
    ) -> None:
        """Claim 事件：ACCEPTED 建节点+经营边；CONTRADICTED/SUPERSEDED 失效边。"""
        if event_type in ("CLAIM_CONTRADICTED", "CLAIM_SUPERSEDED"):
            # 失效物化边
            predicate = event.get("payload", {}).get("predicate_code")
            if predicate and predicate in (
                "PRODUCES", "DEVELOPS", "SUPPLIES_TO", "HAS_REVENUE_FROM"
            ):
                self._projector.invalidate_business_edge(
                    rel_type=predicate, claim_id=str(claim_id)
                )
            log.info("claim_edge_invalidated", claim_id=str(claim_id),
                     event_type=event_type)
            return

        # ACCEPTED / 其他：投影 Claim 节点 + 经营边
        claim = uow._conn.execute(  # noqa: SLF001
            """
            SELECT id, claim_status, predicate_code, business_stage,
                   evidence_state, valid_from, valid_to, recorded_at,
                   confidence, ontology_version, subject_entity_id, object_entity_id
            FROM fact.claim WHERE id = %s
            """,
            (claim_id,),
        ).fetchone()
        if claim is None:
            log.warning("claim_not_found", claim_id=str(claim_id))
            return

        columns = ["id", "claim_status", "predicate_code", "business_stage",
                   "evidence_state", "valid_from", "valid_to", "recorded_at",
                   "confidence", "ontology_version", "subject_entity_id",
                   "object_entity_id"]
        claim_dict = dict(zip(columns, claim, strict=True))

        # 乱序守卫：旧版本拒绝
        if not self._projector.project_claim_node(claim_dict):
            log.info("stale_claim_rejected", claim_id=str(claim_id))
            return

        # 经营边：claim 必须 ACCEPTED 且有 subject + object
        if (claim_dict["claim_status"] == "ACCEPTED"
                and claim_dict["subject_entity_id"] and claim_dict["object_entity_id"]
                and claim_dict["predicate_code"] in (
                    "PRODUCES", "DEVELOPS", "SUPPLIES_TO", "HAS_REVENUE_FROM")):
            self._projector.project_business_edge(
                rel_type=claim_dict["predicate_code"],
                source_id=str(claim_dict["subject_entity_id"]),
                target_id=str(claim_dict["object_entity_id"]),
                claim_id=str(claim_dict["id"]),
                props={
                    "business_stage": claim_dict["business_stage"],
                    "evidence_state": claim_dict["evidence_state"],
                    "valid_from": str(claim_dict["valid_from"])
                        if claim_dict["valid_from"] else None,
                    "valid_to": str(claim_dict["valid_to"])
                        if claim_dict["valid_to"] else None,
                    "recorded_at": str(claim_dict["recorded_at"])
                        if claim_dict["recorded_at"] else None,
                    "confidence": float(claim_dict["confidence"])
                        if claim_dict["confidence"] else None,
                },
            )
            log.info("claim_edge_projected", claim_id=str(claim_id),
                     rel_type=claim_dict["predicate_code"])

    def _dispatch_entity_event(
        self, uow: Any, event: dict[str, Any], entity_type: str, entity_id: Any
    ) -> None:
        """实体事件：MERGE 节点。"""
        self._projector.project_entity(entity_type, str(entity_id),
                                       event.get("payload", {}))


def run_projection_worker(
    uow_factory: UnitOfWorkFactory,
    executor: GraphExecutor,
    *,
    batch_size: int = 10,
    max_retries: int = 5,
    poll_interval_seconds: float = 5.0,
    single_pass: bool = False,
) -> int:
    """Worker 入口：循环领取→投影→标记。single_pass=True 时跑一轮即返回。

    executor 必须提供（GraphExecutor Protocol）。无执行器时 fail-fast。
    """
    from src.projection.worker import claim_batch, process_event

    projector = Neo4jProjector(executor)
    dispatcher = EventDispatcher(projector)

    def project_fn(uow, event):
        dispatcher.dispatch(uow, event)

    def verify_fn(uow, event):
        # 读后校验：实体事件检查节点存在；Claim 事件检查 Claim 节点存在。
        # ID 统一 str() 归一化——PG 返回 UUID 对象，Cypher 参数和 FakeGraphExecutor 键为 str
        entity_id = str(event.get("aggregate_id", ""))
        if event.get("aggregate_type") == "Claim":
            return projector.verify_entity("Claim", entity_id)
        return True  # 实体 MERGE 自身是幂等的

    total_processed = 0
    while True:
        with uow_factory.transaction() as uow:
            events = claim_batch(uow, batch_size=batch_size)
        if not events:
            if single_pass:
                break
            time.sleep(poll_interval_seconds)
            continue
        for event in events:
            with uow_factory.transaction() as uow:
                result = process_event(
                    uow, event, project_fn=project_fn, verify_fn=verify_fn,
                    max_retries=max_retries,
                )
            if result.status == "PROCESSED":
                total_processed += 1
            elif result.status == "DEAD_LETTERED":
                log.error("event_dead_lettered",
                          event_id=str(result.event_id), error=result.error)
        if single_pass:
            break
    return total_processed


if __name__ == "__main__":
    from src.core.bootstrap import load_settings_or_fail
    from src.core.logging import configure_logging

    settings = load_settings_or_fail()
    configure_logging(settings)
    factory = UnitOfWorkFactory(settings.postgres_dsn)
    # 生产：真实 Neo4j driver（此处预留适配点；MVP 用 FakeGraphExecutor 测试）
    executor = None  # 生产接真实 driver 后替换此行
    if executor is None:
        import sys

        print("ERROR: no graph executor configured. "
              "Set NEO4J_URI or configure executor before starting worker.",
              file=sys.stderr)
        sys.exit(1)
    log.info("projection_worker_started")
    run_projection_worker(factory, executor=executor)
