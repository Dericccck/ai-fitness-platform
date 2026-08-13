package com.shuyiwa.fitness.training.api;

import com.shuyiwa.fitness.training.domain.TrainingDay;
import com.shuyiwa.fitness.training.domain.TrainingPlanStatus;

import java.time.Instant;
import java.util.List;

/** 对外返回的计划视图，保留状态、版本和审核事实，供 Agent 和前端展示。 */
public class TrainingPlanView {
    private String id;
    private String organizationId;
    private String studentId;
    private String coachId;
    private String title;
    private String goalType;
    private String source;
    private TrainingPlanStatus status;
    private int version;
    private String createdBy;
    private String reviewedBy;
    private String publishedBy;
    private String reviewComment;
    private Instant createdAt;
    private Instant updatedAt;
    private Instant reviewedAt;
    private Instant publishedAt;
    private List<TrainingDay> days;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getOrganizationId() { return organizationId; }
    public void setOrganizationId(String organizationId) { this.organizationId = organizationId; }
    public String getStudentId() { return studentId; }
    public void setStudentId(String studentId) { this.studentId = studentId; }
    public String getCoachId() { return coachId; }
    public void setCoachId(String coachId) { this.coachId = coachId; }
    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }
    public String getGoalType() { return goalType; }
    public void setGoalType(String goalType) { this.goalType = goalType; }
    public String getSource() { return source; }
    public void setSource(String source) { this.source = source; }
    public TrainingPlanStatus getStatus() { return status; }
    public void setStatus(TrainingPlanStatus status) { this.status = status; }
    public int getVersion() { return version; }
    public void setVersion(int version) { this.version = version; }
    public String getCreatedBy() { return createdBy; }
    public void setCreatedBy(String createdBy) { this.createdBy = createdBy; }
    public String getReviewedBy() { return reviewedBy; }
    public void setReviewedBy(String reviewedBy) { this.reviewedBy = reviewedBy; }
    public String getPublishedBy() { return publishedBy; }
    public void setPublishedBy(String publishedBy) { this.publishedBy = publishedBy; }
    public String getReviewComment() { return reviewComment; }
    public void setReviewComment(String reviewComment) { this.reviewComment = reviewComment; }
    public Instant getCreatedAt() { return createdAt; }
    public void setCreatedAt(Instant createdAt) { this.createdAt = createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(Instant updatedAt) { this.updatedAt = updatedAt; }
    public Instant getReviewedAt() { return reviewedAt; }
    public void setReviewedAt(Instant reviewedAt) { this.reviewedAt = reviewedAt; }
    public Instant getPublishedAt() { return publishedAt; }
    public void setPublishedAt(Instant publishedAt) { this.publishedAt = publishedAt; }
    public List<TrainingDay> getDays() { return days; }
    public void setDays(List<TrainingDay> days) { this.days = days; }
}
