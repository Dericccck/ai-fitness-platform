package com.shuyiwa.fitness.training.api;

import com.shuyiwa.fitness.training.domain.TrainingDayExecutionStatus;

/** 学员提交训练日执行结果的结构化请求；状态由白名单枚举约束。 */
public class TrainingDayExecutionRequest {
    private String dayId;
    private TrainingDayExecutionStatus status;
    private String note;

    public String getDayId() {
        return dayId;
    }

    public void setDayId(String dayId) {
        this.dayId = dayId;
    }

    public TrainingDayExecutionStatus getStatus() {
        return status;
    }

    public void setStatus(TrainingDayExecutionStatus status) {
        this.status = status;
    }

    public String getNote() {
        return note;
    }

    public void setNote(String note) {
        this.note = note;
    }
}
