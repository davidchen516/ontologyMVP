-- ontologyMVP 数据库角色脚本（issue #2）
-- 三类角色的权限边界：
--   ontology_api_readonly : API 查询进程，只读（SELECT），禁止一切写入与 DDL
--   ontology_worker_writer : Worker/采集进程，业务表读写（SELECT/INSERT/UPDATE），
--                            禁止 DELETE（历史不物理删除）、禁止 DDL、禁止管理 alembic_version
--   ontology_migrator      : 独立迁移进程专用，Schema/表结构与 alembic_version 全权
--
-- 本脚本由超级用户或数据库属主执行，幂等可重复。登录账号另行创建并
-- GRANT 组角色（示例见 tests/db/test_roles.py），密码只通过环境变量注入。
-- 应用进程不得持有迁移权限（ADR：迁移是独立步骤）。

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ontology_api_readonly') THEN
        CREATE ROLE ontology_api_readonly NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ontology_worker_writer') THEN
        CREATE ROLE ontology_worker_writer NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ontology_migrator') THEN
        CREATE ROLE ontology_migrator NOLOGIN;
    END IF;
END
$$;

-- =============== API 只读角色 ===============
GRANT USAGE ON SCHEMA raw, master, fact, finance, ops TO ontology_api_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA raw, master, fact, finance, ops
    TO ontology_api_readonly;
GRANT SELECT ON alembic_version TO ontology_api_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw GRANT SELECT ON TABLES TO ontology_api_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA master GRANT SELECT ON TABLES TO ontology_api_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA fact GRANT SELECT ON TABLES TO ontology_api_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA finance GRANT SELECT ON TABLES TO ontology_api_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT SELECT ON TABLES TO ontology_api_readonly;

-- =============== Worker 受限写入角色 ===============
GRANT USAGE ON SCHEMA raw, master, fact, finance, ops TO ontology_worker_writer;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA raw, master, fact, finance, ops
    TO ontology_worker_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw
    GRANT SELECT, INSERT, UPDATE ON TABLES TO ontology_worker_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA master
    GRANT SELECT, INSERT, UPDATE ON TABLES TO ontology_worker_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA fact
    GRANT SELECT, INSERT, UPDATE ON TABLES TO ontology_worker_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA finance
    GRANT SELECT, INSERT, UPDATE ON TABLES TO ontology_worker_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops
    GRANT SELECT, INSERT, UPDATE ON TABLES TO ontology_worker_writer;
-- 刻意不授予：DELETE（历史 Claim/Evidence/Provenance 不物理删除）、
-- TRUNCATE、DDL、alembic_version 写权限

-- =============== 迁移专用角色 ===============
GRANT USAGE, CREATE ON SCHEMA raw, master, fact, finance, ops TO ontology_migrator;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA raw, master, fact, finance, ops
    TO ontology_migrator;
GRANT SELECT, INSERT, UPDATE, DELETE ON alembic_version TO ontology_migrator;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw
    GRANT ALL ON TABLES TO ontology_migrator;
ALTER DEFAULT PRIVILEGES IN SCHEMA master
    GRANT ALL ON TABLES TO ontology_migrator;
ALTER DEFAULT PRIVILEGES IN SCHEMA fact
    GRANT ALL ON TABLES TO ontology_migrator;
ALTER DEFAULT PRIVILEGES IN SCHEMA finance
    GRANT ALL ON TABLES TO ontology_migrator;
ALTER DEFAULT PRIVILEGES IN SCHEMA ops
    GRANT ALL ON TABLES TO ontology_migrator;
-- 迁移角色对数据库本体还需要 CREATE（新建 Schema/扩展）——由 DBA 执行
-- GRANT CREATE ON DATABASE <dbname> TO ontology_migrator; 视部署最小化授予
