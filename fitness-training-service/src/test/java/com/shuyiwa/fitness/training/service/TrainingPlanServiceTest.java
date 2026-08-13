package com.shuyiwa.fitness.training.service;

import com.shuyiwa.fitness.training.api.TrainingPlanRequest;
import com.shuyiwa.fitness.training.domain.TrainingDay;
import com.shuyiwa.fitness.training.domain.TrainingItem;
import com.shuyiwa.fitness.training.domain.TrainingPlan;
import com.shuyiwa.fitness.training.repository.TrainingPlanRepository;
import com.shuyiwa.fitness.training.security.TrainingActor;
import org.junit.Test;

import java.util.Collections;
import java.util.HashSet;

import static org.junit.Assert.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
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
        when(repository.findById(any(String.class))).thenAnswer(invocation -> {
            TrainingPlan saved = new TrainingPlan();
            saved.setId(invocation.getArgument(0));
            saved.setStatus(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.DRAFT);
            return java.util.Optional.of(saved);
        });

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

        TrainingActor actor = new TrainingActor("student-1",
                Collections.singleton(TrainingActor.STUDENT), Collections.singleton("org-1"), "req-1");
        assertEquals(com.shuyiwa.fitness.training.domain.TrainingPlanStatus.DRAFT,
                service.createAgentDraft(actor, request).getStatus());
        verify(repository).insertDraft(any(TrainingPlan.class));
    }
}
