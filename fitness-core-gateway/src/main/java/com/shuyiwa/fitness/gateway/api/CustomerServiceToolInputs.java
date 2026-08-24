package com.shuyiwa.fitness.gateway.api;

/** 客服工单 Tool Gateway 输入契约；状态和创建人不能由 Agent 传入。 */
public final class CustomerServiceToolInputs {

    private CustomerServiceToolInputs() {}

    public static class CreateInput {
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
}
