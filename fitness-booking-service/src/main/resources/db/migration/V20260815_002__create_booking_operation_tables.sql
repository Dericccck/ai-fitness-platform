-- Booking Agent 改约和取消预约的幂等记录。
-- 表创建使用 IF NOT EXISTS；Outbox 的列扩展由 Java 元数据检查器完成，
-- 因为 MySQL 8 不支持 ALTER TABLE ADD COLUMN IF NOT EXISTS。

CREATE TABLE IF NOT EXISTS agent_booking_reschedule_operation (
    request_id VARCHAR(128) NOT NULL,
    appointment_id VARCHAR(32) NOT NULL,
    organization_id VARCHAR(32) NOT NULL,
    actor_id VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (request_id),
    UNIQUE KEY uk_agent_booking_reschedule_appointment_request (appointment_id, request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Booking Agent 改约幂等记录';

CREATE TABLE IF NOT EXISTS agent_booking_cancel_operation (
    request_id VARCHAR(128) NOT NULL,
    appointment_id VARCHAR(32) NOT NULL,
    organization_id VARCHAR(32) NOT NULL,
    actor_id VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (request_id),
    UNIQUE KEY uk_agent_booking_cancel_appointment (appointment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Booking Agent 取消预约幂等记录';
