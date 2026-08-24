SET @customer_ticket_payload_hash_exists = (
    SELECT COUNT(1) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'agent_customer_service_ticket'
      AND column_name = 'payload_hash'
);
SET @customer_ticket_payload_hash_sql = IF(
    @customer_ticket_payload_hash_exists = 0,
    'ALTER TABLE agent_customer_service_ticket ADD COLUMN payload_hash CHAR(64) NULL COMMENT ''确认动作绑定的规范化参数摘要'' AFTER create_request_id',
    'SELECT 1'
);
PREPARE customer_ticket_payload_hash_statement FROM @customer_ticket_payload_hash_sql;
EXECUTE customer_ticket_payload_hash_statement;
DEALLOCATE PREPARE customer_ticket_payload_hash_statement;

SET @customer_ticket_source_exists = (
    SELECT COUNT(1) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'agent_customer_service_ticket'
      AND column_name = 'source'
);
SET @customer_ticket_source_sql = IF(
    @customer_ticket_source_exists = 0,
    'ALTER TABLE agent_customer_service_ticket ADD COLUMN source VARCHAR(24) NOT NULL DEFAULT ''AGENT'' COMMENT ''工单来源；当前由 Agent 受控创建，不由模型传入'' AFTER category',
    'SELECT 1'
);
PREPARE customer_ticket_source_statement FROM @customer_ticket_source_sql;
EXECUTE customer_ticket_source_statement;
DEALLOCATE PREPARE customer_ticket_source_statement;

CREATE TABLE IF NOT EXISTS agent_customer_service_confirmation_consumption (
    jti VARCHAR(128) NOT NULL PRIMARY KEY COMMENT '一次性确认凭证 JTI',
    confirmation_id VARCHAR(64) NOT NULL COMMENT '确认单标识',
    tool_id VARCHAR(128) NOT NULL COMMENT '内部稳定工具标识',
    action VARCHAR(64) NOT NULL COMMENT '确认动作',
    organization_id VARCHAR(64) NOT NULL COMMENT '确认时绑定的机构',
    resource VARCHAR(256) NOT NULL COMMENT '确认时绑定的资源范围',
    request_id VARCHAR(128) NOT NULL COMMENT '业务请求幂等标识',
    payload_hash CHAR(64) NOT NULL COMMENT '确认参数摘要',
    consumed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '凭证消费时间',
    KEY idx_customer_service_confirmation_request (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='客服工单确认凭证一次性消费审计';

CREATE TABLE IF NOT EXISTS agent_customer_service_ticket_audit (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '审计记录编号',
    ticket_id VARCHAR(64) NOT NULL COMMENT '客服工单标识',
    action VARCHAR(64) NOT NULL COMMENT '业务动作',
    actor_id VARCHAR(64) NOT NULL COMMENT '执行主体标识',
    request_id VARCHAR(128) NOT NULL COMMENT '业务请求幂等标识',
    from_status VARCHAR(24) NULL COMMENT '变更前工单状态',
    to_status VARCHAR(24) NOT NULL COMMENT '变更后工单状态',
    comment VARCHAR(1000) NULL COMMENT '管理员处理说明',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '审计时间',
    KEY idx_customer_service_ticket_audit_ticket (ticket_id, created_at),
    UNIQUE KEY uk_customer_service_ticket_audit_request (request_id, action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='客服工单不可变状态审计';

INSERT IGNORE INTO customer_service_schema_version(version) VALUES ('V20260824_002');
