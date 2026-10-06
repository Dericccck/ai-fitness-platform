ALTER TABLE agent_training_outbox
    ADD COLUMN replay_count INT NOT NULL DEFAULT 0,
    ADD COLUMN last_replayed_by VARCHAR(64) NULL,
    ADD COLUMN last_replayed_at TIMESTAMP NULL;

CREATE TABLE IF NOT EXISTS agent_training_outbox_replay_audit (
    id BIGINT NOT NULL AUTO_INCREMENT,
    outbox_id BIGINT NOT NULL,
    operator_id VARCHAR(64) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_agent_training_outbox_replay (outbox_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO training_schema_version (version) VALUES ('V20260917_009');
