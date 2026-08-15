package com.shuyiwa.fitness.booking.security;

/** Gateway 已验签并转发的确认声明，业务事务会再次校验其操作范围。 */
public final class BookingConfirmation {
    private final String confirmationId;
    private final String jti;
    private final String toolId;
    private final String action;
    private final String organizationId;
    private final String resource;
    private final String payloadHash;

    public BookingConfirmation(String confirmationId, String jti, String toolId, String action,
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
