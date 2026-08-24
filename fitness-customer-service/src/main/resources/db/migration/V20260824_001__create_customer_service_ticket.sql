SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS customer_service_schema_version (
    version VARCHAR(64) NOT NULL PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='客服服务结构迁移版本记录';

CREATE TABLE IF NOT EXISTS agent_customer_service_ticket (
    id VARCHAR(64) NOT NULL PRIMARY KEY COMMENT '客服工单唯一标识',
    organization_id VARCHAR(64) NOT NULL COMMENT '所属健身机构标识',
    subject_user_id VARCHAR(64) NOT NULL COMMENT '提出问题或被服务的用户标识',
    created_by_user_id VARCHAR(64) NOT NULL COMMENT '创建工单的主体标识，后续写入使用',
    category VARCHAR(32) NOT NULL COMMENT '工单分类，例如规则咨询、预约问题、训练计划问题',
    source VARCHAR(24) NOT NULL DEFAULT 'AGENT' COMMENT '工单来源；当前由 Agent 受控创建，不由模型传入',
    subject VARCHAR(255) NOT NULL COMMENT '工单标题',
    description TEXT NOT NULL COMMENT '用户问题和结构化上下文摘要',
    status VARCHAR(24) NOT NULL DEFAULT 'OPEN' COMMENT '工单状态：OPEN、IN_PROGRESS、RESOLVED、CLOSED',
    resolution VARCHAR(1000) NULL COMMENT '管理员处理结论，当前只预留，不由 Agent 自动填写',
    related_resource_type VARCHAR(64) NULL COMMENT '关联资源类型，例如 APPOINTMENT、TRAINING_PLAN',
    related_resource_id VARCHAR(128) NULL COMMENT '关联资源标识',
    create_request_id VARCHAR(128) NULL COMMENT '未来创建接口的幂等请求标识',
    payload_hash CHAR(64) NULL COMMENT '确认动作绑定的规范化参数摘要',
    version BIGINT NOT NULL DEFAULT 0 COMMENT '乐观锁版本，后续处理工单时使用',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    resolved_at TIMESTAMP NULL COMMENT '解决时间',
    UNIQUE KEY uk_customer_service_ticket_request (create_request_id),
    KEY idx_customer_service_ticket_subject (organization_id, subject_user_id, created_at),
    KEY idx_customer_service_ticket_status (organization_id, status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='健身客服工单业务事实；创建需确认、幂等和审计，处理状态由客服侧维护';

INSERT IGNORE INTO customer_service_schema_version(version) VALUES ('V20260824_001');
