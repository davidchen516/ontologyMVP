"""financial observation identity includes announced_at; scope mapping fix

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-29

独立审查 BLOCKER 修复：#4 的唯一键缺 announced_at，导致同
(security, metric, period, report_type, update_flag) 的财务重述被
ON CONFLICT DO NOTHING 静默丢弃——违反"历史不得覆盖、重述需可区分"。
本修订把 announced_at 纳入观察值身份：重述行独立保留，由当前值视图按
最新公告日期选择。

同时修正视图口径映射：report_type '6' 归入 PARENT 桶（原实现落入
UNKNOWN，与声明的 CONSOLIDATED > PARENT > UNKNOWN 偏好不符）。

回滚策略：downgrade 恢复原唯一键（不含 announced_at）与原视图定义。
注意：downgrade 会因重复行失败——如已摄入重述数据，需先按业务合并。
"""

from __future__ import annotations

from alembic import op

revision = "0005_financial_announced_at"
down_revision = "0004_standardization_layer"
branch_labels = None
depends_on = None

_OLD_KEY_COLUMNS = "security_id, metric_code, period_end, report_type, update_flag"
_NEW_KEY_COLUMNS = "security_id, metric_code, period_end, report_type, update_flag, announced_at"

_NEW_VIEW = """
CREATE OR REPLACE VIEW finance.v_financial_observation_current AS
SELECT o.*
FROM (
    SELECT fo.*,
           row_number() OVER (
               PARTITION BY fo.security_id, fo.metric_code, fo.period_end
               ORDER BY
                   CASE fo.report_type
                       WHEN '1' THEN 1 WHEN '4' THEN 1
                       WHEN '2' THEN 2 WHEN '5' THEN 2 WHEN '6' THEN 2
                       ELSE 3
                   END ASC,
                   fo.announced_at DESC NULLS LAST,
                   fo.update_flag DESC NULLS LAST,
                   fo.recorded_at DESC
           ) AS version_rank
    FROM finance.financial_observation fo
) o
WHERE o.version_rank = 1
"""

_OLD_VIEW = """
CREATE OR REPLACE VIEW finance.v_financial_observation_current AS
SELECT o.*
FROM (
    SELECT fo.*,
           row_number() OVER (
               PARTITION BY fo.security_id, fo.metric_code, fo.period_end
               ORDER BY
                   CASE fo.report_type
                       WHEN '1' THEN 1 WHEN '4' THEN 1
                       WHEN '2' THEN 2 WHEN '5' THEN 2
                       ELSE 3
                   END ASC,
                   fo.announced_at DESC NULLS LAST,
                   fo.update_flag DESC NULLS LAST,
                   fo.recorded_at DESC
           ) AS version_rank
    FROM finance.financial_observation fo
) o
WHERE o.version_rank = 1
"""


def _drop_unique_constraint() -> None:
    # 0002 创建的复合唯一约束未命名 → 按定义动态定位后删除
    op.execute(
        """
        DO $$
        DECLARE cname text;
        BEGIN
            SELECT conname INTO cname
            FROM pg_constraint
            WHERE conrelid = 'finance.financial_observation'::regclass
              AND contype = 'u';
            IF cname IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE finance.financial_observation DROP CONSTRAINT %I',
                    cname
                );
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    _drop_unique_constraint()
    op.execute(
        "ALTER TABLE finance.financial_observation "
        "ADD CONSTRAINT financial_observation_identity "
        "UNIQUE NULLS NOT DISTINCT ("
        + _NEW_KEY_COLUMNS
        + ")"
    )
    op.execute(_NEW_VIEW)


def downgrade() -> None:
    op.execute(_OLD_VIEW)
    _drop_unique_constraint()
    op.execute(
        "ALTER TABLE finance.financial_observation "
        "ADD CONSTRAINT financial_observation_identity_old "
        "UNIQUE NULLS NOT DISTINCT ("
        + _OLD_KEY_COLUMNS
        + ")"
    )
