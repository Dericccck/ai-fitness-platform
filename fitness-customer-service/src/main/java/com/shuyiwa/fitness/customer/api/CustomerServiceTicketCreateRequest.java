package com.shuyiwa.fitness.customer.api;

/**
 * 创建客服工单的稳定输入契约。
 *
 * <p>subjectUserId 允许管理员代用户发起问题；普通学员/教练最终会在业务服务中被
 * 强制绑定为自己的用户 ID。状态、创建人、机构和请求 ID 不接受请求体传入。</p>
 */
public class CustomerServiceTicketCreateRequest {

    private String organizationId;
    private String subjectUserId;
    private String category;
    private String subject;
    private String description;
    private String relatedResourceType;
    private String relatedResourceId;

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
    public String getRelatedResourceType() { return relatedResourceType; }
    public void setRelatedResourceType(String relatedResourceType) { this.relatedResourceType = relatedResourceType; }
    public String getRelatedResourceId() { return relatedResourceId; }
    public void setRelatedResourceId(String relatedResourceId) { this.relatedResourceId = relatedResourceId; }
}
