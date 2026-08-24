package com.shuyiwa.fitness.gateway.config;

import com.shuyiwa.fitness.gateway.api.ToolViews;

import java.time.Instant;

/** 客服服务内部响应 DTO，转换后才允许进入 Agent Tool View。 */
final class CustomerServiceViews {

    private CustomerServiceViews() {}

    static final class Ticket {
        public String id;
        public String organizationId;
        public String subjectUserId;
        public String createdByUserId;
        public String category;
        public String source;
        public String subject;
        public String description;
        public String status;
        public String relatedResourceType;
        public String relatedResourceId;
        public Instant createdAt;
        public Instant updatedAt;
        public Instant resolvedAt;

        ToolViews.CustomerServiceTicketView toToolView() {
            return new ToolViews.CustomerServiceTicketView(id, organizationId, subjectUserId,
                    createdByUserId, category, source, subject, description, status, relatedResourceType,
                    relatedResourceId, createdAt, updatedAt, resolvedAt);
        }
    }
}
