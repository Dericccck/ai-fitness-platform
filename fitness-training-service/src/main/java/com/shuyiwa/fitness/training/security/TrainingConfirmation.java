package com.shuyiwa.fitness.training.security;

/**
 * Gateway 已验签后传入训练服务的一次性确认声明。
 *
 * <p>训练服务不重新解析外部 Token，而是信任经过内部服务 Token 保护的 Gateway 转发结果。
 * 它仍会把声明和本次业务动作绑定，并在同一数据库事务中消费 jti，避免“验签成功但同一凭证
 * 被重放两次”。</p>
 */
public final class TrainingConfirmation {

    private final String confirmationId;
    private final String jti;
    private final String toolId;
    private final String action;
    private final String organizationId;
    private final String resource;
    private final String payloadHash;

    public TrainingConfirmation(String confirmationId, String jti, String toolId, String action,
                                String organizationId, String resource, String payloadHash) {
        this.confirmationId = confirmationId;
        this.jti = jti;
        this.toolId = toolId;
        this.action = action;
        this.organizationId = organizationId;
        this.resource = resource;
        this.payloadHash = payloadHash;
    }

    public String getConfirmationId() { return confirmationId; }
    public String getJti() { return jti; }
    public String getToolId() { return toolId; }
    public String getAction() { return action; }
    public String getOrganizationId() { return organizationId; }
    public String getResource() { return resource; }
    public String getPayloadHash() { return payloadHash; }
}
