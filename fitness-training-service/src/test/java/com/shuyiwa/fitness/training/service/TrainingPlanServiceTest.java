package com.shuyiwa.fitness.training.service;

import com.shuyiwa.fitness.training.api.TrainingPlanRequest;
import com.shuyiwa.fitness.training.api.TrainingDayExecutionRequest;
import com.shuyiwa.fitness.training.api.TrainingDayExecutionView;
import com.shuyiwa.fitness.training.domain.TrainingDay;
import com.shuyiwa.fitness.training.domain.TrainingDayExecution;
import com.shuyiwa.fitness.training.domain.TrainingDayExecutionStatus;
import com.shuyiwa.fitness.training.domain.TrainingItem;
import com.shuyiwa.fitness.training.domain.TrainingPlan;
import com.shuyiwa.fitness.training.repository.TrainingPlanRepository;
import com.shuyiwa.fitness.training.security.TrainingActor;
import com.shuyiwa.fitness.training.security.TrainingConfirmation;
import org.junit.Test;

import java.util.Collections;
import java.util.HashSet;

import static org.junit.Assert.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.verify;

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
        TrainingActor actor = new TrainingActor("student-1",
                Collections.singleton(TrainingActor.STUDENT), Collections.singleton("org-1"), "req-1",
                confirmation);
        assertEquals(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.DRAFT,
                service.createAgentDraft(actor, request).getStatus());
        verify(repository).insertDraft(any(TrainingPlan.class), org.mockito.ArgumentMatchers.eq("req-1"),
                any(TrainingConfirmation.class));
    }

    @Test
    public void creatingDraftWithoutConfirmationIsRejectedBeforeRepositoryWrite() {
        TrainingPlanRepository repository = mock(TrainingPlanRepository.class);
        TrainingPlanService service = new TrainingPlanService(repository);
        when(repository.isOrganizationMember("org-1", "student-1")).thenReturn(true);
        when(repository.isCoachForStudent("org-1", "coach-1", "student-1")).thenReturn(true);

        try {
            service.createAgentDraft(new TrainingActor("student-1",
                    Collections.singleton(TrainingActor.STUDENT), Collections.singleton("org-1"), "req-1"),
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
