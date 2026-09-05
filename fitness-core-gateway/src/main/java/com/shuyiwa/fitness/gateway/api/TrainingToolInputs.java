package com.shuyiwa.fitness.gateway.api;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;

/**
 * Gateway 到训练服务的结构化输入 DTO。
 *
 * <p>这些 DTO 不接收状态、审核人和发布人字段；状态由训练服务状态机产生。确认凭证
 * 也不放进业务 JSON，而是由 Gateway 从独立 Header 校验并透传。</p>
 */
public final class TrainingToolInputs {

    private TrainingToolInputs() {}

    public static class DraftInput {
        private String organizationId;
        private String studentId;
        private String coachId;
        private String title;
        private String goalType;
        private Integer sessionMinutes;
        private List<String> availableEquipment = new ArrayList<>();
        private String constraints;
        private List<DayInput> days = new ArrayList<>();

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
        public List<DayInput> getDays() { return days; }
        public void setDays(List<DayInput> days) { this.days = days == null ? new ArrayList<>() : days; }
    }

    public static class DayInput {
        private Integer dayNumber;
        private String title;
        private LocalDate scheduledDate;
        private List<ItemInput> items = new ArrayList<>();

        public Integer getDayNumber() { return dayNumber; }
        public void setDayNumber(Integer dayNumber) { this.dayNumber = dayNumber; }
        public String getTitle() { return title; }
        public void setTitle(String title) { this.title = title; }
        public LocalDate getScheduledDate() { return scheduledDate; }
        public void setScheduledDate(LocalDate scheduledDate) { this.scheduledDate = scheduledDate; }
        public List<ItemInput> getItems() { return items; }
        public void setItems(List<ItemInput> items) { this.items = items == null ? new ArrayList<>() : items; }
    }

    public static class ItemInput {
        private String exerciseName;
        private Integer sortOrder;
        private Integer sets;
        private String reps;
        private Integer restSeconds;
        private BigDecimal targetWeightKg;
        private BigDecimal targetRpe;
        private String notes;

        public String getExerciseName() { return exerciseName; }
        public void setExerciseName(String exerciseName) { this.exerciseName = exerciseName; }
        public Integer getSortOrder() { return sortOrder; }
        public void setSortOrder(Integer sortOrder) { this.sortOrder = sortOrder; }
        public Integer getSets() { return sets; }
        public void setSets(Integer sets) { this.sets = sets; }
        public String getReps() { return reps; }
        public void setReps(String reps) { this.reps = reps; }
        public Integer getRestSeconds() { return restSeconds; }
        public void setRestSeconds(Integer restSeconds) { this.restSeconds = restSeconds; }
        public BigDecimal getTargetWeightKg() { return targetWeightKg; }
        public void setTargetWeightKg(BigDecimal targetWeightKg) { this.targetWeightKg = targetWeightKg; }
        public BigDecimal getTargetRpe() { return targetRpe; }
        public void setTargetRpe(BigDecimal targetRpe) { this.targetRpe = targetRpe; }
        public String getNotes() { return notes; }
        public void setNotes(String notes) { this.notes = notes; }
    }

    public static class ReviewInput {
        private String decision;
        private String comment;

        public String getDecision() { return decision; }
        public void setDecision(String decision) { this.decision = decision; }
        public String getComment() { return comment; }
        public void setComment(String comment) { this.comment = comment; }
    }

    /** 学员训练日执行结果；业务服务只接受 COMPLETED 或 SKIPPED。 */
    public static class ExecutionInput {
        private String dayId;
        private String status;
        private String note;

        public String getDayId() { return dayId; }
        public void setDayId(String dayId) { this.dayId = dayId; }
        public String getStatus() { return status; }
        public void setStatus(String status) { this.status = status; }
        public String getNote() { return note; }
        public void setNote(String note) { this.note = note; }
    }
}
