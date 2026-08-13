-- 训练领域第一版迁移：只保存健身计划闭环，不触碰赛事、作品和活动遗留表。
-- 计划正文采用计划/训练日/动作三级结构，避免把可执行动作埋在一段模型生成文本里。

CREATE TABLE IF NOT EXISTS training_plan (
    id VARCHAR(32) NOT NULL,
    organization_id VARCHAR(32) NOT NULL,
    student_id VARCHAR(32) NOT NULL,
    coach_id VARCHAR(32) NOT NULL,
    title VARCHAR(128) NOT NULL,
    goal_type VARCHAR(32) NOT NULL,
    source VARCHAR(16) NOT NULL,
    status VARCHAR(24) NOT NULL,
    version INT NOT NULL DEFAULT 0,
    created_by VARCHAR(32) NOT NULL,
    reviewed_by VARCHAR(32) NULL,
    published_by VARCHAR(32) NULL,
    review_comment VARCHAR(1000) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP NULL,
    published_at TIMESTAMP NULL,
    PRIMARY KEY (id),
    KEY idx_training_plan_scope (organization_id, student_id, status),
    KEY idx_training_plan_coach (organization_id, coach_id, status),
    CONSTRAINT chk_training_plan_source CHECK (source IN ('AGENT', 'COACH')),
    CONSTRAINT chk_training_plan_status CHECK (status IN ('DRAFT', 'PENDING_REVIEW', 'APPROVED', 'REJECTED', 'PUBLISHED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='结构化健身训练计划聚合根';

CREATE TABLE IF NOT EXISTS training_plan_day (
    id VARCHAR(32) NOT NULL,
    plan_id VARCHAR(32) NOT NULL,
    day_number INT NOT NULL,
    title VARCHAR(128) NOT NULL,
    scheduled_date DATE NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_training_plan_day_number (plan_id, day_number),
    CONSTRAINT fk_training_plan_day_plan FOREIGN KEY (plan_id) REFERENCES training_plan (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='训练计划中的训练日';

CREATE TABLE IF NOT EXISTS training_plan_item (
    id VARCHAR(32) NOT NULL,
    day_id VARCHAR(32) NOT NULL,
    exercise_name VARCHAR(128) NOT NULL,
    sort_order INT NOT NULL,
    sets_count INT NOT NULL,
    reps VARCHAR(64) NOT NULL,
    rest_seconds INT NULL,
    target_weight_kg DECIMAL(8,2) NULL,
    target_rpe DECIMAL(4,1) NULL,
    notes VARCHAR(1000) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_training_plan_item_order (day_id, sort_order),
    CONSTRAINT fk_training_plan_item_day FOREIGN KEY (day_id) REFERENCES training_plan_day (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='训练日中的结构化动作处方';

CREATE TABLE IF NOT EXISTS training_plan_audit (
    id BIGINT NOT NULL AUTO_INCREMENT,
    plan_id VARCHAR(32) NOT NULL,
    action VARCHAR(32) NOT NULL,
    actor_id VARCHAR(32) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    from_status VARCHAR(24) NULL,
    to_status VARCHAR(24) NOT NULL,
    comment VARCHAR(1000) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_training_plan_audit_plan (plan_id, created_at),
    UNIQUE KEY uk_training_plan_audit_request (request_id),
    CONSTRAINT fk_training_plan_audit_plan FOREIGN KEY (plan_id) REFERENCES training_plan (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='训练计划状态转换不可变审计';

CREATE TABLE IF NOT EXISTS training_schema_version (
    version VARCHAR(64) NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='训练服务迁移版本记录';

INSERT IGNORE INTO training_schema_version (version) VALUES ('V20260813_001');
