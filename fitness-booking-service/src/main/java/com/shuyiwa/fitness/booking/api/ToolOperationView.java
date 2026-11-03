package com.shuyiwa.fitness.booking.api;

/** Immutable-operation identity and a durable outcome; UNKNOWN never means not executed. */
public final class ToolOperationView {
    private String operationId;
    private String toolId;
    private String confirmationId;
    private String organizationId;
    private String actorId;
    private String payloadHash;
    private String status;
    private String resultReference;
    public ToolOperationView() {}
    public ToolOperationView(String operationId, String toolId, String confirmationId, String organizationId, String actorId, String payloadHash, String status, String resultReference) {
        this.operationId = operationId;
        this.toolId = toolId;
        this.confirmationId = confirmationId;
        this.organizationId = organizationId;
        this.actorId = actorId;
        this.payloadHash = payloadHash;
        this.status = status;
        this.resultReference = resultReference;
    }
    public String getOperationId() { return operationId; }
    public void setOperationId(String value) { operationId = value; }
    public String getToolId() { return toolId; }
    public void setToolId(String value) { toolId = value; }
    public String getConfirmationId() { return confirmationId; }
    public void setConfirmationId(String value) { confirmationId = value; }
    public String getOrganizationId() { return organizationId; }
    public void setOrganizationId(String value) { organizationId = value; }
    public String getActorId() { return actorId; }
    public void setActorId(String value) { actorId = value; }
    public String getPayloadHash() { return payloadHash; }
    public void setPayloadHash(String value) { payloadHash = value; }
    public String getStatus() { return status; }
    public void setStatus(String value) { status = value; }
    public String getResultReference() { return resultReference; }
    public void setResultReference(String value) { resultReference = value; }
}
