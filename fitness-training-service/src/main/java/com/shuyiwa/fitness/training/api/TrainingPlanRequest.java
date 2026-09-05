package com.shuyiwa.fitness.training.api;

import com.shuyiwa.fitness.training.domain.TrainingDay;

import java.util.ArrayList;
import java.util.List;

/**
 * 结构化计划输入。
 *
 * <p>这里故意不接收 status、createdBy、reviewedBy 等服务器字段。Agent 只能提交计划内容，
 * 状态和审计字段由训练服务根据已认证主体和状态机计算。</p>
 */
public class TrainingPlanRequest {
    private String organizationId;
    private String studentId;
    private String coachId;
    private String title;
    private String goalType;
    private Integer sessionMinutes;
    private List<String> availableEquipment = new ArrayList<>();
    private String constraints;
    private List<TrainingDay> days = new ArrayList<>();

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
    public Integer getSessionMinutes() { return sessionMinutes; }
    public void setSessionMinutes(Integer sessionMinutes) { this.sessionMinutes = sessionMinutes; }
    public List<String> getAvailableEquipment() { return availableEquipment; }
    public void setAvailableEquipment(List<String> availableEquipment) {
        this.availableEquipment = availableEquipment == null ? new ArrayList<>() : availableEquipment;
    }
    public String getConstraints() { return constraints; }
    public void setConstraints(String constraints) { this.constraints = constraints; }
    public List<TrainingDay> getDays() { return days; }
    public void setDays(List<TrainingDay> days) { this.days = days == null ? new ArrayList<>() : days; }
}
