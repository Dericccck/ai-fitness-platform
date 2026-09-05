-- 通知乱序判断使用业务聚合版本，不能只根据事件类型推断先后。
-- 旧事件以版本 1 回填；新事件在训练计划状态事务内写入更新后的真实版本。
ALTER TABLE agent_training_outbox
    ADD COLUMN aggregate_version INT NOT NULL DEFAULT 1 AFTER organization_id;

INSERT IGNORE INTO training_schema_version (version) VALUES ('V20260906_006');
