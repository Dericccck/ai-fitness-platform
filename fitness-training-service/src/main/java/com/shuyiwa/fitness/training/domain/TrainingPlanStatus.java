package com.shuyiwa.fitness.training.domain;

/** 训练计划的持久化状态。状态迁移只能由业务 Service 统一执行。 */
public enum TrainingPlanStatus {
    DRAFT,
    PENDING_REVIEW,
    APPROVED,
    REJECTED,
    PUBLISHED;

    /**
     * 显式列出允许的状态迁移，避免 Controller 通过字符串直接修改状态。
     * Agent 只能创建 DRAFT；PUBLISHED 没有回退路径，修改必须产生新版本。
     */
    public boolean canTransitionTo(TrainingPlanStatus target) {
        if (this == DRAFT || this == REJECTED) {
            return target == PENDING_REVIEW;
        }
        if (this == PENDING_REVIEW) {
            return target == APPROVED || target == REJECTED;
        }
        return this == APPROVED && target == PUBLISHED;
    }
}
