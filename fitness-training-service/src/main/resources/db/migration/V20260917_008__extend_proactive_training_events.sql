-- 训练执行事件为主动式 Agent 提供可靠业务事实；仍与训练写入处于同一 MySQL 事务。
ALTER TABLE agent_training_outbox DROP CHECK chk_agent_training_outbox_type;
ALTER TABLE agent_training_outbox ADD CONSTRAINT chk_agent_training_outbox_type CHECK (
    event_type IN (
        'TRAINING_PLAN_REVIEW_REQUIRED', 'TRAINING_PLAN_PUBLISHED',
        'TRAINING_DAY_COMPLETED', 'TRAINING_DAY_SKIPPED', 'TRAINING_PLAN_COMPLETED'
    )
);

INSERT IGNORE INTO training_schema_version (version) VALUES ('V20260917_008');
