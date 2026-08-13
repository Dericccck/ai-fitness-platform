package com.shuyiwa.fitness.training.domain;

/**
 * 训练日执行结果。
 *
 * <p>本阶段只记录训练日级结果，不记录逐组重量、疼痛、疲劳或身体测量。未执行不是数据库
 * 中的一条记录，而是查询不到执行记录时由业务层推导出的默认状态；数据库只保存学员明确
 * 提交的已完成或已跳过事实。</p>
 */
public enum TrainingDayExecutionStatus {
    /** 学员完成了该训练日。 */
    COMPLETED,
    /** 学员明确跳过了该训练日。 */
    SKIPPED
}
