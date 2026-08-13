package com.shuyiwa.fitness.training.domain;

public class TrainingItem {
    private String id;
    private String exerciseName;
    private Integer sortOrder;
    private Integer sets;
    private String reps;
    private Integer restSeconds;
    private java.math.BigDecimal targetWeightKg;
    private java.math.BigDecimal targetRpe;
    private String notes;

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
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
    public java.math.BigDecimal getTargetWeightKg() { return targetWeightKg; }
    public void setTargetWeightKg(java.math.BigDecimal targetWeightKg) { this.targetWeightKg = targetWeightKg; }
    public java.math.BigDecimal getTargetRpe() { return targetRpe; }
    public void setTargetRpe(java.math.BigDecimal targetRpe) { this.targetRpe = targetRpe; }
    public String getNotes() { return notes; }
    public void setNotes(String notes) { this.notes = notes; }
}
