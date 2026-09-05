-- 训练服务的受控动作策略目录。未知或停用动作默认不得由 Agent 写入计划。
-- 所有中文列和预置数据使用 utf8mb4，避免不同部署环境的连接默认编码造成乱码。
CREATE TABLE IF NOT EXISTS training_exercise_policy (
    id VARCHAR(32) NOT NULL,
    normalized_name VARCHAR(128) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    required_equipment VARCHAR(128) NULL,
    blocked_constraint_keywords VARCHAR(512) NULL,
    active TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_training_exercise_policy_name (normalized_name),
    KEY idx_training_exercise_policy_active (active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent训练动作及器械禁忌策略目录';

INSERT IGNORE INTO training_exercise_policy
    (id, normalized_name, display_name, required_equipment, blocked_constraint_keywords)
VALUES
    ('exercise-squat', '深蹲', '深蹲', NULL, '膝痛,膝关节术后'),
    ('exercise-body-squat', '徒手深蹲', '徒手深蹲', NULL, '膝痛,膝关节术后'),
    ('exercise-band-squat', '弹力带深蹲', '弹力带深蹲', '弹力带', '膝痛,膝关节术后'),
    ('exercise-band-row', '弹力带划船', '弹力带划船', '弹力带', '肩痛,肩关节术后'),
    ('exercise-push-up', '俯卧撑', '俯卧撑', NULL, '腕痛,肩痛,腕关节术后,肩关节术后'),
    ('exercise-plank', '平板支撑', '平板支撑', NULL, '腕痛,肩痛,腰痛'),
    ('exercise-dumbbell-press', '哑铃卧推', '哑铃卧推', '哑铃', '肩痛,肩关节术后'),
    ('exercise-barbell-squat', '杠铃深蹲', '杠铃深蹲', '杠铃', '膝痛,腰痛,膝关节术后'),
    ('exercise-treadmill-walk', '跑步机快走', '跑步机快走', '跑步机', '膝痛,踝痛,心脏不适');

INSERT IGNORE INTO training_schema_version (version) VALUES ('V20260906_007');
