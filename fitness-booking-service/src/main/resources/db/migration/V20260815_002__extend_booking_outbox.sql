-- Outbox 发布器需要租约、重试次数和最后错误信息。
-- MySQL 8 支持 IF NOT EXISTS，服务重复启动时不会重复增加列。

ALTER TABLE agent_booking_outbox
    ADD COLUMN IF NOT EXISTS attempt_count INT NOT NULL DEFAULT 0 AFTER status,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMP NULL AFTER attempt_count,
    ADD COLUMN IF NOT EXISTS last_error VARCHAR(2000) NULL AFTER next_attempt_at,
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP NULL AFTER last_error,
    ADD COLUMN IF NOT EXISTS claimed_by VARCHAR(128) NULL AFTER claimed_at;

-- 索引由 BookingDatabaseInitializer 查询元数据后幂等创建，避免不同 MySQL 版本的语法差异。

CREATE TABLE IF NOT EXISTS agent_booking_reschedule_operation (
    request_id VARCHAR(128) NOT NULL,
    appointment_id VARCHAR(32) NOT NULL,
    organization_id VARCHAR(32) NOT NULL,
    actor_id VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (request_id),
    UNIQUE KEY uk_agent_booking_reschedule_appointment_request (appointment_id, request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Booking Agent 改约幂等记录';
