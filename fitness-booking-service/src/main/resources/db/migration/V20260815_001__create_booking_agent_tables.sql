-- Booking Agent 首批写服务表。
-- 只增加 Agent 操作的幂等、确认消费、审计和 Outbox 表，不修改旧预约表结构。

CREATE TABLE IF NOT EXISTS agent_booking_operation (
    request_id VARCHAR(128) NOT NULL,
    appointment_id VARCHAR(32) NOT NULL,
    organization_id VARCHAR(32) NOT NULL,
    actor_id VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (request_id),
    UNIQUE KEY uk_agent_booking_operation_appointment (appointment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Booking Agent 创建预约幂等记录';

CREATE TABLE IF NOT EXISTS agent_booking_confirmation_consumption (
    jti VARCHAR(128) NOT NULL,
    confirmation_id VARCHAR(64) NOT NULL,
    tool_id VARCHAR(128) NOT NULL,
    action VARCHAR(128) NOT NULL,
    organization_id VARCHAR(32) NOT NULL,
    resource VARCHAR(128) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    consumed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (jti),
    UNIQUE KEY uk_agent_booking_confirmation_request (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Booking Agent 确认凭证一次性消费记录';

CREATE TABLE IF NOT EXISTS agent_booking_audit (
    id BIGINT NOT NULL AUTO_INCREMENT,
    appointment_id VARCHAR(32) NOT NULL,
    organization_id VARCHAR(32) NOT NULL,
    action VARCHAR(64) NOT NULL,
    actor_id VARCHAR(32) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_booking_audit_request (request_id),
    KEY idx_agent_booking_audit_appointment (appointment_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Booking Agent 预约写操作不可变审计';

CREATE TABLE IF NOT EXISTS agent_booking_outbox (
    id BIGINT NOT NULL AUTO_INCREMENT,
    event_key VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    aggregate_id VARCHAR(32) NOT NULL,
    organization_id VARCHAR(32) NOT NULL,
    payload JSON NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_booking_outbox_event (event_key),
    KEY idx_agent_booking_outbox_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='预约创建事件 Outbox，供通知或消息适配器异步消费';

CREATE TABLE IF NOT EXISTS agent_booking_resource_lock (
    organization_id VARCHAR(32) NOT NULL,
    resource_type VARCHAR(16) NOT NULL,
    resource_id VARCHAR(64) NOT NULL,
    resource_date DATE NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (organization_id, resource_type, resource_id, resource_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='预约资源日期锁记录，行锁随事务释放';
