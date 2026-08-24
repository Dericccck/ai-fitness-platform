-- 训练计划主动提醒事件 Outbox；状态变化和事件写入必须位于同一个 MySQL 事务。
CREATE TABLE IF NOT EXISTS agent_training_outbox (
    id BIGINT NOT NULL AUTO_INCREMENT,
    -- 事件键由事件类型、计划 ID 和请求 ID 组成，必须覆盖请求 ID 的 128 字符上限。
    event_key VARCHAR(255) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    aggregate_id VARCHAR(32) NOT NULL,
    organization_id VARCHAR(32) NOT NULL,
    payload JSON NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    attempt_count INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMP NULL,
    claimed_at TIMESTAMP NULL,
    claimed_by VARCHAR(128) NULL,
    published_at TIMESTAMP NULL,
    last_error VARCHAR(2000) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_training_outbox_event (event_key),
    KEY idx_agent_training_outbox_status (status, next_attempt_at, created_at),
    CONSTRAINT chk_agent_training_outbox_type CHECK (
        event_type IN ('TRAINING_PLAN_REVIEW_REQUIRED', 'TRAINING_PLAN_PUBLISHED')
    ),
    CONSTRAINT chk_agent_training_outbox_status CHECK (status IN ('PENDING', 'PUBLISHED', 'DEAD'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='训练计划主动提醒事件 Outbox';

INSERT IGNORE INTO training_schema_version (version) VALUES ('V20260824_005');
