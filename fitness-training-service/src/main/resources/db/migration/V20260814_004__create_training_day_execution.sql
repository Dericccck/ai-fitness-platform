-- 最小训练执行闭环：只记录学员对已发布训练日的完成或跳过结果。
-- 当前状态表用于查询，审计表保留每次成功状态变更；两张表与 JTI 消费在同一事务中写入。

CREATE TABLE IF NOT EXISTS training_day_execution (
    id VARCHAR(32) NOT NULL,
    plan_id VARCHAR(32) NOT NULL,
    day_id VARCHAR(32) NOT NULL,
    organization_id VARCHAR(64) NOT NULL,
    student_id VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    execution_date DATE NOT NULL,
    note VARCHAR(1000) NULL,
    version INT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_training_day_execution_day (plan_id, day_id),
    KEY idx_training_day_execution_student (organization_id, student_id, execution_date),
    CONSTRAINT fk_training_day_execution_plan FOREIGN KEY (plan_id) REFERENCES training_plan (id),
    CONSTRAINT fk_training_day_execution_day FOREIGN KEY (day_id) REFERENCES training_plan_day (id),
    CONSTRAINT chk_training_day_execution_status CHECK (status IN ('COMPLETED', 'SKIPPED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='学员训练日完成或跳过的当前状态';

CREATE TABLE IF NOT EXISTS training_day_execution_audit (
    id BIGINT NOT NULL AUTO_INCREMENT,
    execution_id VARCHAR(32) NOT NULL,
    plan_id VARCHAR(32) NOT NULL,
    day_id VARCHAR(32) NOT NULL,
    action VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    actor_id VARCHAR(64) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    note VARCHAR(1000) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_training_day_execution_audit_request (request_id),
    KEY idx_training_day_execution_audit_day (plan_id, day_id, created_at),
    CONSTRAINT fk_training_day_execution_audit_execution FOREIGN KEY (execution_id)
        REFERENCES training_day_execution (id),
    CONSTRAINT chk_training_day_execution_audit_status CHECK (status IN ('COMPLETED', 'SKIPPED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='训练日执行状态变更不可变审计';

INSERT IGNORE INTO training_schema_version (version) VALUES ('V20260814_004');
