package com.shuyiwa.fitness.training.api;

import com.shuyiwa.fitness.training.domain.TrainingDayExecutionStatus;

import java.time.Instant;
import java.time.LocalDate;

/** 训练日执行记录的稳定返回视图，不暴露数据库内部审计列。 */
public class TrainingDayExecutionView {
    private String id;
    private String planId;
    private String dayId;
    private String organizationId;
    private String studentId;
    private TrainingDayExecutionStatus status;
    private LocalDate executionDate;
    private String note;
    private int version;
    private Instant createdAt;
    private Instant updatedAt;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getPlanId() { return planId; }
    public void setPlanId(String planId) { this.planId = planId; }
    public String getDayId() { return dayId; }
    public void setDayId(String dayId) { this.dayId = dayId; }
    public String getOrganizationId() { return organizationId; }
    public void setOrganizationId(String organizationId) { this.organizationId = organizationId; }
    public String getStudentId() { return studentId; }
    public void setStudentId(String studentId) { this.studentId = studentId; }
    public TrainingDayExecutionStatus getStatus() { return status; }
    public void setStatus(TrainingDayExecutionStatus status) { this.status = status; }
    public LocalDate getExecutionDate() { return executionDate; }
    public void setExecutionDate(LocalDate executionDate) { this.executionDate = executionDate; }
    public String getNote() { return note; }
    public void setNote(String note) { this.note = note; }
    public int getVersion() { return version; }
    public void setVersion(int version) { this.version = version; }
    public Instant getCreatedAt() { return createdAt; }
    public void setCreatedAt(Instant createdAt) { this.createdAt = createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(Instant updatedAt) { this.updatedAt = updatedAt; }
}
