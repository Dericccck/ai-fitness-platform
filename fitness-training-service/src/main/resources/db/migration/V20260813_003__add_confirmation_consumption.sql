-- 确认凭证消费审计：JTI 只允许被业务服务成功领取一次。
-- 该表与训练计划写入处于同一个事务；业务事务回滚时，消费记录也会回滚。

CREATE TABLE IF NOT EXISTS training_confirmation_consumption (
    jti VARCHAR(128) NOT NULL,
    confirmation_id VARCHAR(64) NOT NULL,
    tool_id VARCHAR(128) NOT NULL,
    action VARCHAR(64) NOT NULL,
    organization_id VARCHAR(64) NOT NULL,
    resource VARCHAR(256) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    consumed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (jti),
    KEY idx_training_confirmation_request (request_id),
    KEY idx_training_confirmation_id (confirmation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='训练计划确认凭证一次性消费审计';

INSERT IGNORE INTO training_schema_version (version) VALUES ('V20260813_003');
