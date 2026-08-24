package com.shuyiwa.fitness.customer.security;

/** Gateway 已验签后透传的客服工单确认声明；工单服务负责在事务中消费 JTI。 */
public final class CustomerServiceConfirmation {

    private final String confirmationId;
    private final String jti;
    private final String toolId;
    private final String action;
    private final String organizationId;
    private final String resource;
    private final String payloadHash;

    public CustomerServiceConfirmation(String confirmationId, String jti, String toolId, String action,
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
