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

    /** 结构化训练计划的稳定 Tool View，不把训练服务内部 DTO 直接暴露给 Agent。 */
    public static final class TrainingPlanView {
        private final String id;
        private final String organizationId;
        private final String studentId;
        private final String coachId;
        private final String title;
        private final String goalType;
        private final String source;
        private final String status;
        private final int version;
        private final String createdBy;
        private final String reviewedBy;
        private final String publishedBy;
        private final String reviewComment;
        private final Instant createdAt;
        private final Instant updatedAt;
        private final Instant reviewedAt;
        private final Instant publishedAt;
        private final java.util.List<TrainingDayView> days;

        public TrainingPlanView(String id, String organizationId, String studentId, String coachId,
                                String title, String goalType, String source, String status, int version,
                                String createdBy, String reviewedBy, String publishedBy, String reviewComment,
                                Instant createdAt, Instant updatedAt, Instant reviewedAt, Instant publishedAt,
                                java.util.List<TrainingDayView> days) {
            this.id = id; this.organizationId = organizationId; this.studentId = studentId; this.coachId = coachId;
            this.title = title; this.goalType = goalType; this.source = source; this.status = status;
            this.version = version; this.createdBy = createdBy; this.reviewedBy = reviewedBy;
            this.publishedBy = publishedBy; this.reviewComment = reviewComment; this.createdAt = createdAt;
            this.updatedAt = updatedAt; this.reviewedAt = reviewedAt; this.publishedAt = publishedAt;
            this.days = days;
        }

        public String getId() { return id; }
        public String getOrganizationId() { return organizationId; }
        public String getStudentId() { return studentId; }
        public String getCoachId() { return coachId; }
        public String getTitle() { return title; }
        public String getGoalType() { return goalType; }
        public String getSource() { return source; }
        public String getStatus() { return status; }
        public int getVersion() { return version; }
        public String getCreatedBy() { return createdBy; }
        public String getReviewedBy() { return reviewedBy; }
        public String getPublishedBy() { return publishedBy; }
        public String getReviewComment() { return reviewComment; }
        public Instant getCreatedAt() { return createdAt; }
        public Instant getUpdatedAt() { return updatedAt; }
        public Instant getReviewedAt() { return reviewedAt; }
        public Instant getPublishedAt() { return publishedAt; }
        public java.util.List<TrainingDayView> getDays() { return days; }
    }

    public static final class TrainingDayView {
        private final String id;
        private final Integer dayNumber;
        private final String title;
        private final java.time.LocalDate scheduledDate;
        private final java.util.List<TrainingItemView> items;

        public TrainingDayView(String id, Integer dayNumber, String title, java.time.LocalDate scheduledDate,
                               java.util.List<TrainingItemView> items) {
            this.id = id; this.dayNumber = dayNumber; this.title = title; this.scheduledDate = scheduledDate;
            this.items = items;
        }

        public String getId() { return id; }
        public Integer getDayNumber() { return dayNumber; }
        public String getTitle() { return title; }
        public java.time.LocalDate getScheduledDate() { return scheduledDate; }
        public java.util.List<TrainingItemView> getItems() { return items; }
    }

    public static final class TrainingItemView {
        private final String id;
        private final String exerciseName;
        private final Integer sortOrder;
        private final Integer sets;
        private final String reps;
        private final Integer restSeconds;
        private final java.math.BigDecimal targetWeightKg;
        private final java.math.BigDecimal targetRpe;
        private final String notes;

        public TrainingItemView(String id, String exerciseName, Integer sortOrder, Integer sets, String reps,
                                Integer restSeconds, java.math.BigDecimal targetWeightKg,
                                java.math.BigDecimal targetRpe, String notes) {
            this.id = id; this.exerciseName = exerciseName; this.sortOrder = sortOrder; this.sets = sets;
            this.reps = reps; this.restSeconds = restSeconds; this.targetWeightKg = targetWeightKg;
            this.targetRpe = targetRpe; this.notes = notes;
        }

        public String getId() { return id; }
        public String getExerciseName() { return exerciseName; }
        public Integer getSortOrder() { return sortOrder; }
        public Integer getSets() { return sets; }
        public String getReps() { return reps; }
        public Integer getRestSeconds() { return restSeconds; }
        public java.math.BigDecimal getTargetWeightKg() { return targetWeightKg; }
        public java.math.BigDecimal getTargetRpe() { return targetRpe; }
        public String getNotes() { return notes; }
    }

    /** 学员训练日执行结果的稳定 Tool View；未执行训练日不会伪造一条记录。 */
    public static final class TrainingDayExecutionView {
        private final String id;
        private final String planId;
        private final String dayId;
        private final String organizationId;
        private final String studentId;
        private final String status;
        private final java.time.LocalDate executionDate;
        private final String note;
        private final int version;
        private final Instant createdAt;
        private final Instant updatedAt;

        public TrainingDayExecutionView(String id, String planId, String dayId, String organizationId,
                                        String studentId, String status, java.time.LocalDate executionDate,
                                        String note, int version, Instant createdAt, Instant updatedAt) {
            this.id = id;
            this.planId = planId;
            this.dayId = dayId;
            this.organizationId = organizationId;
            this.studentId = studentId;
            this.status = status;
            this.executionDate = executionDate;
            this.note = note;
            this.version = version;
            this.createdAt = createdAt;
            this.updatedAt = updatedAt;
        }

        public String getId() { return id; }
        public String getPlanId() { return planId; }
        public String getDayId() { return dayId; }
        public String getOrganizationId() { return organizationId; }
        public String getStudentId() { return studentId; }
        public String getStatus() { return status; }
        public java.time.LocalDate getExecutionDate() { return executionDate; }
        public String getNote() { return note; }
        public int getVersion() { return version; }
        public Instant getCreatedAt() { return createdAt; }
        public Instant getUpdatedAt() { return updatedAt; }
    }
}
