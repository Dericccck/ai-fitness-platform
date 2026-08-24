package com.shuyiwa.fitness.customer.api;

import java.time.Instant;

/** 对 Gateway 暴露的稳定工单视图，不暴露内部审计字段或任意数据库列。 */
public class CustomerServiceTicketView {

    private String id;
    private String organizationId;
    private String subjectUserId;
    private String category;
    private String subject;
    private String description;
    private String status;
    private String relatedResourceType;
    private String relatedResourceId;
    private Instant createdAt;
    private Instant updatedAt;
    private Instant resolvedAt;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getOrganizationId() { return organizationId; }
    public void setOrganizationId(String organizationId) { this.organizationId = organizationId; }
    public String getSubjectUserId() { return subjectUserId; }
    public void setSubjectUserId(String subjectUserId) { this.subjectUserId = subjectUserId; }
    public String getCategory() { return category; }
    public void setCategory(String category) { this.category = category; }
    public String getSubject() { return subject; }
    public void setSubject(String subject) { this.subject = subject; }
    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getRelatedResourceType() { return relatedResourceType; }
    public void setRelatedResourceType(String relatedResourceType) { this.relatedResourceType = relatedResourceType; }
    public String getRelatedResourceId() { return relatedResourceId; }
    public void setRelatedResourceId(String relatedResourceId) { this.relatedResourceId = relatedResourceId; }
    public Instant getCreatedAt() { return createdAt; }
    public void setCreatedAt(Instant createdAt) { this.createdAt = createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(Instant updatedAt) { this.updatedAt = updatedAt; }
    public Instant getResolvedAt() { return resolvedAt; }
    public void setResolvedAt(Instant resolvedAt) { this.resolvedAt = resolvedAt; }
}
