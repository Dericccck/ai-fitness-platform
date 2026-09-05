-- 生产迁移 Job 使用的预约事件聚合版本列；本地启动器会通过 information_schema 幂等补列。
ALTER TABLE agent_booking_outbox
    ADD COLUMN aggregate_version BIGINT NOT NULL DEFAULT 1 AFTER organization_id;
