-- DEAD Outbox 受控重放审计。重复执行安全，payload 不复制到审计表。
CREATE TABLE IF NOT EXISTS agent_booking_outbox_replay_audit (
    id BIGINT NOT NULL AUTO_INCREMENT,
    outbox_id BIGINT NOT NULL,
    operator_id VARCHAR(32) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_agent_booking_outbox_replay_audit_outbox (outbox_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='预约 Outbox DEAD 受控重放审计';
