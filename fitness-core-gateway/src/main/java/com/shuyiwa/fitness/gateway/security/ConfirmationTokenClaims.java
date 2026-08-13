package com.shuyiwa.fitness.gateway.security;

/**
 * Gateway 验证后的确认凭证声明。
 *
 * <p>声明不是授权事实本身，而是由受信任 Agent 服务签名的不可变执行边界。Gateway
 * 负责校验范围，训练服务还会在自己的事务中消费 jti，不能只因为验签成功就认为业务已经执行。</p>
 */
public final class ConfirmationTokenClaims {

    private final String confirmationId;
    private final String toolId;
    private final String action;
    private final String subjectUserId;
    private final String organizationId;
    private final String resource;
    private final String requestId;
    private final String payloadHash;
    private final String jti;
    private final long expiresAt;

    public ConfirmationTokenClaims(String confirmationId, String toolId, String action,
                                   String subjectUserId, String organizationId, String resource,
                                   String requestId, String payloadHash, String jti,
                                   long expiresAt) {
        this.confirmationId = confirmationId;
        this.toolId = toolId;
        this.action = action;
        this.subjectUserId = subjectUserId;
        this.organizationId = organizationId;
        this.resource = resource;
        this.requestId = requestId;
        this.payloadHash = payloadHash;
        this.jti = jti;
        this.expiresAt = expiresAt;
    }

    public String getConfirmationId() { return confirmationId; }
    public String getToolId() { return toolId; }
    public String getAction() { return action; }
    public String getSubjectUserId() { return subjectUserId; }
    public String getOrganizationId() { return organizationId; }
    public String getResource() { return resource; }
    public String getRequestId() { return requestId; }
    public String getPayloadHash() { return payloadHash; }
    public String getJti() { return jti; }
    public long getExpiresAt() { return expiresAt; }
}
