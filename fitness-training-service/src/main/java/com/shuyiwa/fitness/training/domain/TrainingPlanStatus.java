package com.shuyiwa.fitness.training.domain;

/**
 * 训练计划的持久化业务状态。
 *
 * <p>这些值描述的是训练计划生命周期，不是 Agent 的授权状态。Agent 可以提出草案，
 * 但最终状态转换必须经过训练服务的角色、学员-教练关系、版本号、幂等请求和事务校验。</p>
 */
public enum TrainingPlanStatus {
    /** Agent 或教练刚创建的草案，学员不能执行。 */
    DRAFT,
    /** 已提交给负责教练审核，等待审核结果，仍不能执行。 */
    PENDING_REVIEW,
    /** 教练审核通过，但只有发布后才允许学员执行。 */
    APPROVED,
    /** 教练驳回，必须修改草案后重新提交，不能直接发布。 */
    REJECTED,
    /** 已发布的正式计划，学员可读取并执行；正式版本不原地篡改。 */
    PUBLISHED;

    /**
     * 显式列出允许的状态迁移，避免 Controller 通过字符串直接修改状态。
     *
     * <p>DRAFT/REJECTED -> PENDING_REVIEW 表示重新提交；PENDING_REVIEW -> APPROVED 或
     * REJECTED 表示教练审核；APPROVED -> PUBLISHED 表示发布。PUBLISHED 没有回退路径，
     * 修改必须产生新计划或新版本，避免学员正在执行的内容被静默替换。</p>
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
