package com.shuyiwa.fitness.training.service;

import com.shuyiwa.fitness.training.api.TrainingPlanRequest;
import com.shuyiwa.fitness.training.api.TrainingPlanView;
import com.shuyiwa.fitness.training.api.TrainingDayExecutionRequest;
import com.shuyiwa.fitness.training.api.TrainingDayExecutionView;
import com.shuyiwa.fitness.training.api.TrainingReviewRequest;
import com.shuyiwa.fitness.training.domain.TrainingDay;
import com.shuyiwa.fitness.training.domain.TrainingDayExecution;
import com.shuyiwa.fitness.training.domain.TrainingDayExecutionStatus;
import com.shuyiwa.fitness.training.domain.TrainingItem;
import com.shuyiwa.fitness.training.domain.TrainingPlan;
import com.shuyiwa.fitness.training.repository.TrainingPlanRepository;
import com.shuyiwa.fitness.training.security.TrainingActor;
import com.shuyiwa.fitness.training.security.TrainingConfirmation;
import org.springframework.http.HttpStatus;
import org.junit.Test;

import java.util.Collections;
import java.util.HashSet;

import static org.junit.Assert.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.verify;
import static org.mockito.ArgumentMatchers.eq;

/** 训练计划权限和结构化输入测试，防止 Agent 直接生成正式发布计划。 */
public class TrainingPlanServiceTest {

    @Test
    public void creatingDraftNeverSkipsReview() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);
        when(repository.isOrganizationMember("org-1", "student-1")).thenReturn(true);
        when(repository.isCoachForStudent("org-1", "coach-1", "student-1")).thenReturn(true);
        when(repository.insertDraft(any(TrainingPlan.class), org.mockito.ArgumentMatchers.eq("req-1"),
                any(TrainingConfirmation.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(repository.findById(any(String.class))).thenAnswer(invocation -> {
            TrainingPlan saved = new TrainingPlan();
            saved.setId(invocation.getArgument(0));
            saved.setStatus(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.DRAFT);
            return java.util.Optional.of(saved);
        });

        TrainingPlanRequest request = validDraftRequest();

        TrainingConfirmation confirmation = new TrainingConfirmation(
                "confirmation-1", "jti-1", "fitness.training.plan.create_draft.v1",
                "CREATE_TRAINING_DRAFT", "org-1", "org-1:student-1",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        );
        TrainingActor actor = new TrainingActor("coach-1",
                Collections.singleton(TrainingActor.COACH), Collections.singleton("org-1"), "req-1",
                confirmation);
        assertEquals(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.DRAFT,
                service.createAgentDraft(actor, request).getStatus());
        verify(repository).insertDraft(any(TrainingPlan.class), org.mockito.ArgumentMatchers.eq("req-1"),
                any(TrainingConfirmation.class));
    }

    @Test
    public void studentCannotCreateDraftEvenForSelf() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);

        try {
            service.createAgentDraft(new TrainingActor("student-1",
                    Collections.singleton(TrainingActor.STUDENT), Collections.singleton("org-1"), "req-1",
                    new TrainingConfirmation(
                            "confirmation-1", "jti-1", "fitness.training.plan.create_draft.v1",
                            "CREATE_TRAINING_DRAFT", "org-1", "org-1:student-1",
                            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    )), validDraftRequest());
        } catch (com.shuyiwa.fitness.training.api.TrainingApiException exception) {
            assertEquals(org.springframework.http.HttpStatus.FORBIDDEN, exception.getStatus());
            verify(repository, never()).insertDraft(any(TrainingPlan.class),
                    org.mockito.ArgumentMatchers.eq("req-1"), any(TrainingConfirmation.class));
            return;
        }
        throw new AssertionError("学员即使为本人也不能创建训练计划草案");
    }

    @Test
    public void creatingDraftWithoutConfirmationIsRejectedBeforeRepositoryWrite() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);
        when(repository.isOrganizationMember("org-1", "student-1")).thenReturn(true);
        when(repository.isCoachForStudent("org-1", "coach-1", "student-1")).thenReturn(true);

        try {
            service.createAgentDraft(new TrainingActor("coach-1",
                    Collections.singleton(TrainingActor.COACH), Collections.singleton("org-1"), "req-1"),
                    validDraftRequest());
        } catch (com.shuyiwa.fitness.training.api.TrainingApiException exception) {
            assertEquals(org.springframework.http.HttpStatus.UNAUTHORIZED, exception.getStatus());
            verify(repository, never()).insertDraft(any(TrainingPlan.class),
                    org.mockito.ArgumentMatchers.eq("req-1"), any(TrainingConfirmation.class));
            return;
        }
        throw new AssertionError("缺少确认凭证时必须拒绝创建草案");
    }

    @Test
    public void onlyStudentCanRecordExecutionForPublishedPlan() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);
        TrainingPlan plan = new TrainingPlan();
        plan.setId("plan-1");
        plan.setOrganizationId("org-1");
        plan.setStudentId("student-1");
        plan.setCoachId("coach-1");
        plan.setStatus(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PUBLISHED);
        TrainingDay day = new TrainingDay();
        day.setId("day-1");
        day.setDayNumber(1);
        day.setTitle("下肢训练");
        plan.setDays(Collections.singletonList(day));
        when(repository.findById("plan-1")).thenReturn(java.util.Optional.of(plan));
        TrainingDayExecution execution = new TrainingDayExecution();
        execution.setId("execution-1");
        execution.setPlanId("plan-1");
        execution.setDayId("day-1");
        execution.setOrganizationId("org-1");
        execution.setStudentId("student-1");
        execution.setStatus(TrainingDayExecutionStatus.COMPLETED);
        execution.setVersion(0);
        when(repository.recordDayExecution(any(String.class), any(String.class), any(String.class),
                any(String.class), any(TrainingDayExecutionStatus.class), any(java.time.LocalDate.class),
                org.mockito.ArgumentMatchers.any(), any(String.class), any(String.class),
                any(TrainingConfirmation.class))).thenReturn(execution);

        TrainingDayExecutionRequest request = new TrainingDayExecutionRequest();
        request.setDayId("day-1");
        request.setStatus(TrainingDayExecutionStatus.COMPLETED);
        TrainingConfirmation confirmation = new TrainingConfirmation(
                "confirmation-1", "jti-1", "fitness.training.day.record_execution.v1",
                "RECORD_TRAINING_DAY_EXECUTION", "org-1", "plan-1:day-1",
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        );
        TrainingDayExecutionView result = service.recordExecution(new TrainingActor("student-1",
                Collections.singleton(TrainingActor.STUDENT), Collections.singleton("org-1"), "req-1",
                confirmation), "plan-1", "day-1", request);

        assertEquals("day-1", result.getDayId());
        assertEquals(TrainingDayExecutionStatus.COMPLETED, result.getStatus());
        verify(repository).recordDayExecution(any(String.class), any(String.class), any(String.class),
                any(String.class), any(TrainingDayExecutionStatus.class), any(java.time.LocalDate.class),
                org.mockito.ArgumentMatchers.any(), any(String.class), any(String.class),
                any(TrainingConfirmation.class));
    }

    @Test
    public void idempotentTransitionStillRequiresConfirmation() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);
        TrainingPlan plan = plan("plan-1", "student-1", "coach-1",
                com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PENDING_REVIEW);
        when(repository.findById("plan-1")).thenReturn(java.util.Optional.of(plan));
        when(repository.wasRequestApplied("plan-1", "req-replay")).thenReturn(true);

        expectStatus(HttpStatus.UNAUTHORIZED, () -> service.review(
                new TrainingActor("coach-1", Collections.singleton(TrainingActor.COACH),
                        Collections.singleton("org-1"), "req-replay"),
                "plan-1", reviewRequest("APPROVE", null)));

        // 幂等命中只能跳过二次写入，不能跳过确认凭证验证，更不能再次写审计。
        verify(repository, never()).transition(any(TrainingPlan.class), any(), any(String.class),
                any(String.class), any(String.class), any(), any(TrainingConfirmation.class));
    }

    @Test
    public void responsibleCoachCanSubmitPlanForReview() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);
        TrainingPlan plan = plan("plan-1", "student-1", "coach-1",
                com.shuyiwa.fitness.training.domain.TrainingPlanStatus.DRAFT);
        when(repository.findById("plan-1")).thenReturn(java.util.Optional.of(plan));
        when(repository.wasRequestApplied("plan-1", "req-submit")).thenReturn(false);
        when(repository.transition(any(TrainingPlan.class), eq(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PENDING_REVIEW),
                eq("SUBMIT_REVIEW"), eq("coach-1"), eq("req-submit"), eq(null), any(TrainingConfirmation.class)))
                .thenAnswer(invocation -> {
                    plan.setStatus(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PENDING_REVIEW);
                    return true;
                });

        TrainingPlanView result = service.submitForReview(new TrainingActor("coach-1",
                Collections.singleton(TrainingActor.COACH), Collections.singleton("org-1"), "req-submit",
                confirmation("fitness.training.plan.submit_review.v1", "SUBMIT_TRAINING_REVIEW", "plan-1")),
                "plan-1");

        assertEquals(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PENDING_REVIEW, result.getStatus());
        verify(repository).transition(any(TrainingPlan.class), eq(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PENDING_REVIEW),
                eq("SUBMIT_REVIEW"), eq("coach-1"), eq("req-submit"), eq(null), any(TrainingConfirmation.class));
    }

    @Test
    public void studentCannotReviewPlan() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);
        TrainingPlan plan = plan("plan-1", "student-1", "coach-1",
                com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PENDING_REVIEW);
        when(repository.findById("plan-1")).thenReturn(java.util.Optional.of(plan));

        expectStatus(HttpStatus.FORBIDDEN, () -> service.review(new TrainingActor("student-1",
                Collections.singleton(TrainingActor.STUDENT), Collections.singleton("org-1"), "req-review",
                confirmation("fitness.training.plan.review.v1", "REVIEW_TRAINING_PLAN", "plan-1")),
                "plan-1", reviewRequest("APPROVE", null)));
        verify(repository, never()).transition(any(TrainingPlan.class), any(), any(String.class),
                any(String.class), any(String.class), any(), any(TrainingConfirmation.class));
    }

    @Test
    public void organizationAdminCanApprovePlanAcrossAssignedCoach() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);
        TrainingPlan plan = plan("plan-1", "student-1", "coach-1",
                com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PENDING_REVIEW);
        when(repository.findById("plan-1")).thenReturn(java.util.Optional.of(plan));
        when(repository.wasRequestApplied("plan-1", "req-admin-review")).thenReturn(false);
        when(repository.transition(any(TrainingPlan.class), eq(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.APPROVED),
                eq("REVIEW"), eq("admin-1"), eq("req-admin-review"), eq(null), any(TrainingConfirmation.class)))
                .thenAnswer(invocation -> {
                    plan.setStatus(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.APPROVED);
                    return true;
                });

        TrainingPlanView result = service.review(new TrainingActor("admin-1",
                Collections.singleton(TrainingActor.ORGANIZATION_ADMIN), Collections.singleton("org-1"),
                "req-admin-review", confirmation("fitness.training.plan.review.v1",
                        "REVIEW_TRAINING_PLAN", "plan-1")), "plan-1", reviewRequest("APPROVE", null));

        assertEquals(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.APPROVED, result.getStatus());
        verify(repository).transition(any(TrainingPlan.class), eq(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.APPROVED),
                eq("REVIEW"), eq("admin-1"), eq("req-admin-review"), eq(null), any(TrainingConfirmation.class));
    }

    @Test
    public void unrelatedCoachCannotReviewPlan() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);
        TrainingPlan plan = plan("plan-1", "student-1", "coach-1",
                com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PENDING_REVIEW);
        when(repository.findById("plan-1")).thenReturn(java.util.Optional.of(plan));

        expectStatus(HttpStatus.FORBIDDEN, () -> service.review(new TrainingActor("coach-2",
                Collections.singleton(TrainingActor.COACH), Collections.singleton("org-1"), "req-review",
                confirmation("fitness.training.plan.review.v1", "REVIEW_TRAINING_PLAN", "plan-1")),
                "plan-1", reviewRequest("APPROVE", null)));
        verify(repository, never()).transition(any(TrainingPlan.class), any(), any(String.class),
                any(String.class), any(String.class), any(), any(TrainingConfirmation.class));
    }

    @Test
    public void rejectedReviewMustContainReason() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);
        TrainingPlan plan = plan("plan-1", "student-1", "coach-1",
                com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PENDING_REVIEW);
        when(repository.findById("plan-1")).thenReturn(java.util.Optional.of(plan));

        expectStatus(HttpStatus.BAD_REQUEST, () -> service.review(new TrainingActor("coach-1",
                Collections.singleton(TrainingActor.COACH), Collections.singleton("org-1"), "req-reject",
                confirmation("fitness.training.plan.review.v1", "REVIEW_TRAINING_PLAN", "plan-1")),
                "plan-1", reviewRequest("REJECT", "  ")));
        verify(repository, never()).transition(any(TrainingPlan.class), any(), any(String.class),
                any(String.class), any(String.class), any(), any(TrainingConfirmation.class));
    }

    @Test
    public void responsibleCoachCanPublishApprovedPlan() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);
        TrainingPlan plan = plan("plan-1", "student-1", "coach-1",
                com.shuyiwa.fitness.training.domain.TrainingPlanStatus.APPROVED);
        when(repository.findById("plan-1")).thenReturn(java.util.Optional.of(plan));
        when(repository.wasRequestApplied("plan-1", "req-publish")).thenReturn(false);
        when(repository.transition(any(TrainingPlan.class), eq(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PUBLISHED),
                eq("PUBLISH"), eq("coach-1"), eq("req-publish"), eq(null), any(TrainingConfirmation.class)))
                .thenAnswer(invocation -> {
                    plan.setStatus(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PUBLISHED);
                    return true;
                });

        TrainingPlanView result = service.publish(new TrainingActor("coach-1",
                Collections.singleton(TrainingActor.COACH), Collections.singleton("org-1"), "req-publish",
                confirmation("fitness.training.plan.publish.v1", "PUBLISH_TRAINING_PLAN", "plan-1")),
                "plan-1");

        assertEquals(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PUBLISHED, result.getStatus());
        verify(repository).transition(any(TrainingPlan.class), eq(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.PUBLISHED),
                eq("PUBLISH"), eq("coach-1"), eq("req-publish"), eq(null), any(TrainingConfirmation.class));
    }

    @Test
    public void studentCannotPublishApprovedPlan() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);
        TrainingPlan plan = plan("plan-1", "student-1", "coach-1",
                com.shuyiwa.fitness.training.domain.TrainingPlanStatus.APPROVED);
        when(repository.findById("plan-1")).thenReturn(java.util.Optional.of(plan));

        expectStatus(HttpStatus.FORBIDDEN, () -> service.publish(new TrainingActor("student-1",
                Collections.singleton(TrainingActor.STUDENT), Collections.singleton("org-1"), "req-publish",
                confirmation("fitness.training.plan.publish.v1", "PUBLISH_TRAINING_PLAN", "plan-1")),
                "plan-1"));
        verify(repository, never()).transition(any(TrainingPlan.class), any(), any(String.class),
                any(String.class), any(String.class), any(), any(TrainingConfirmation.class));
    }

    private TrainingPlan plan(String id, String studentId, String coachId,
                              com.shuyiwa.fitness.training.domain.TrainingPlanStatus status) {
        TrainingPlan plan = new TrainingPlan();
        plan.setId(id);
        plan.setOrganizationId("org-1");
        plan.setStudentId(studentId);
        plan.setCoachId(coachId);
        plan.setStatus(status);
        return plan;
    }

    private TrainingConfirmation confirmation(String toolId, String action, String resource) {
        return new TrainingConfirmation("confirmation-1", "jti-1", toolId, action, "org-1", resource,
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    }

    private TrainingReviewRequest reviewRequest(String decision, String comment) {
        TrainingReviewRequest request = new TrainingReviewRequest();
        request.setDecision(decision);
        request.setComment(comment);
        return request;
    }

    private void expectStatus(HttpStatus expected, Runnable action) {
        try {
            action.run();
        } catch (com.shuyiwa.fitness.training.api.TrainingApiException exception) {
            assertEquals(expected, exception.getStatus());
            return;
        }
        throw new AssertionError("预期训练服务返回 " + expected + "，但操作成功");
    }

    private TrainingPlanRequest validDraftRequest() {
        TrainingPlanRequest request = new TrainingPlanRequest();
        request.setOrganizationId("org-1");
        request.setStudentId("student-1");
        request.setCoachId("coach-1");
        request.setTitle("基础力量计划");
        request.setGoalType("基础力量");
        TrainingDay day = new TrainingDay();
        day.setDayNumber(1);
        day.setTitle("下肢训练");
        TrainingItem item = new TrainingItem();
        item.setExerciseName("深蹲");
        item.setSortOrder(1);
        item.setSets(3);
        item.setReps("8-10");
        day.setItems(Collections.singletonList(item));
        request.setDays(Collections.singletonList(day));
        return request;
    }
}
