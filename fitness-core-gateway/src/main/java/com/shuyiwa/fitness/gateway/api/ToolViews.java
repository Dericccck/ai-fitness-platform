package com.shuyiwa.fitness.gateway.api;

import java.time.Instant;

/**
 * Agent 只读工具返回的稳定视图。
 *
 * <p>视图与旧系统实体解耦，避免把密码、内部状态、赛事字段或 JPA 关系图直接暴露
 * 给模型。后续数据库字段变化时，只需要在 Repository 映射层兼容，不改变 Tool 契约。</p>
 */
public final class ToolViews {

    private ToolViews() {
    }

    public static final class UserView {
        private final String id;
        private final String name;
        private final String phone;
        private final String avatar;
        private final String introduction;
        private final boolean enabled;

        public UserView(String id, String name, String phone, String avatar, String introduction, boolean enabled) {
            this.id = id;
            this.name = name;
            this.phone = phone;
            this.avatar = avatar;
            this.introduction = introduction;
            this.enabled = enabled;
        }

        public String getId() { return id; }
        public String getName() { return name; }
        public String getPhone() { return phone; }
        public String getAvatar() { return avatar; }
        public String getIntroduction() { return introduction; }
        public boolean isEnabled() { return enabled; }
    }

    public static final class OrganizationView {
        private final String id;
        private final String name;
        private final String address;
        private final String summary;
        private final String organizationType;

        public OrganizationView(String id, String name, String address, String summary, String organizationType) {
            this.id = id;
            this.name = name;
            this.address = address;
            this.summary = summary;
            this.organizationType = organizationType;
        }

        public String getId() { return id; }
        public String getName() { return name; }
        public String getAddress() { return address; }
        public String getSummary() { return summary; }
        public String getOrganizationType() { return organizationType; }
    }

    public static final class CourseView {
        private final String id;
        private final String name;
        private final String code;
        private final Integer price;
        private final Integer status;

        public CourseView(String id, String name, String code, Integer price, Integer status) {
            this.id = id;
            this.name = name;
            this.code = code;
            this.price = price;
            this.status = status;
        }

        public String getId() { return id; }
        public String getName() { return name; }
        public String getCode() { return code; }
        public Integer getPrice() { return price; }
        public Integer getStatus() { return status; }
    }

    public static final class ContractView {
        private final String id;
        private final String organizationId;
        private final String userId;
        private final String courseId;
        private final String numberId;
        private final String startDate;
        private final String endDate;
        private final Integer totalClassHours;
        private final Integer remainingClassHours;
        private final Integer status;

        public ContractView(
                String id,
                String organizationId,
                String userId,
                String courseId,
                String numberId,
                String startDate,
                String endDate,
                Integer totalClassHours,
                Integer remainingClassHours,
                Integer status
        ) {
            this.id = id;
            this.organizationId = organizationId;
            this.userId = userId;
            this.courseId = courseId;
            this.numberId = numberId;
            this.startDate = startDate;
            this.endDate = endDate;
            this.totalClassHours = totalClassHours;
            this.remainingClassHours = remainingClassHours;
            this.status = status;
        }

        public String getId() { return id; }
        public String getOrganizationId() { return organizationId; }
        public String getUserId() { return userId; }
        public String getCourseId() { return courseId; }
        public String getNumberId() { return numberId; }
        public String getStartDate() { return startDate; }
        public String getEndDate() { return endDate; }
        public Integer getTotalClassHours() { return totalClassHours; }
        public Integer getRemainingClassHours() { return remainingClassHours; }
        public Integer getStatus() { return status; }
    }

    public static final class AppointmentView {
        private final String id;
        private final String organizationId;
        private final String userId;
        private final String coachId;
        private final String courseId;
        private final String courseName;
        private final Instant startTime;
        private final Instant endTime;
        private final Integer status;
        private final String contractId;

        public AppointmentView(
                String id,
                String organizationId,
                String userId,
                String coachId,
                String courseId,
                String courseName,
                Instant startTime,
                Instant endTime,
                Integer status,
                String contractId
        ) {
            this.id = id;
            this.organizationId = organizationId;
            this.userId = userId;
            this.coachId = coachId;
            this.courseId = courseId;
            this.courseName = courseName;
            this.startTime = startTime;
            this.endTime = endTime;
            this.status = status;
            this.contractId = contractId;
        }

        public String getId() { return id; }
        public String getOrganizationId() { return organizationId; }
        public String getUserId() { return userId; }
        public String getCoachId() { return coachId; }
        public String getCourseId() { return courseId; }
        public String getCourseName() { return courseName; }
        public Instant getStartTime() { return startTime; }
        public Instant getEndTime() { return endTime; }
        public Integer getStatus() { return status; }
        public String getContractId() { return contractId; }
    }
}
