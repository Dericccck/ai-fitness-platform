package com.shuyiwa.fitness.training.domain;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

/** 状态机的纯单元测试，不需要启动数据库。 */
public class TrainingPlanStatusTest {

    @Test
    public void agentDraftCanOnlyEnterReview() {
        assertTrue(TrainingPlanStatus.DRAFT.canTransitionTo(TrainingPlanStatus.PENDING_REVIEW));
        assertFalse(TrainingPlanStatus.DRAFT.canTransitionTo(TrainingPlanStatus.PUBLISHED));
    }

    @Test
    public void publishedPlanCannotBeChangedInPlace() {
        assertFalse(TrainingPlanStatus.PUBLISHED.canTransitionTo(TrainingPlanStatus.DRAFT));
        assertFalse(TrainingPlanStatus.PUBLISHED.canTransitionTo(TrainingPlanStatus.PENDING_REVIEW));
    }

    @Test
    public void reviewMustHappenBeforePublish() {
        assertTrue(TrainingPlanStatus.PENDING_REVIEW.canTransitionTo(TrainingPlanStatus.APPROVED));
        assertTrue(TrainingPlanStatus.APPROVED.canTransitionTo(TrainingPlanStatus.PUBLISHED));
        assertFalse(TrainingPlanStatus.PENDING_REVIEW.canTransitionTo(TrainingPlanStatus.PUBLISHED));
    }

    @Test
    public void rejectedPlanCanBeResubmittedButCannotBePublishedDirectly() {
        assertTrue(TrainingPlanStatus.REJECTED.canTransitionTo(TrainingPlanStatus.PENDING_REVIEW));
        assertFalse(TrainingPlanStatus.REJECTED.canTransitionTo(TrainingPlanStatus.PUBLISHED));
    }
}
